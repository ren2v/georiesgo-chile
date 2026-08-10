import geopandas as gpd
import pandas as pd
import numpy as np
from pathlib import Path
from shapely.geometry import Point

# Rutas resueltas según la ubicación de este archivo, no según el directorio
# desde el que se ejecute — así funciona igual con `python geo.py`,
# `pytest` desde cualquier carpeta, o un servidor de producción.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"

geologia = gpd.read_file(DATA_DIR / "geologia" / "geologia.geojson")
fallas = gpd.read_file(DATA_DIR / "fallas" / "fallas_chile.geojson")
sismos = pd.read_csv(DATA_DIR / "sismos" / "sismos_csn.csv")

EXPANSION_ROCA = {
    "metareni": "metareniscas",
    "monzodio": "monzodiorita",
    "metapeli": "metapelitas",
    "metasedi": "metasedimentos",
    "granodio": "granodiorita",
}


def limpiar(valor):
    """Convierte NaN/NA de pandas a None. Necesario porque algunas columnas
    con datos faltantes devuelven float('nan') en vez de None, y NaN no es
    válido en JSON estricto (rompe la respuesta con 500 en producción)."""
    if valor is None:
        return None
    if isinstance(valor, float) and pd.isna(valor):
        return None
    return valor


def expandir(codigo):
    codigo = limpiar(codigo)
    if codigo is None:
        return None
    return EXPANSION_ROCA.get(codigo, codigo)


def consultar_geologia(lat: float, lng: float) -> dict:
    punto = Point(lng, lat)
    resultado = geologia[geologia.geometry.contains(punto)]

    if resultado.empty:
        return {"encontrado": False}

    fila = resultado.iloc[0]
    rocas = [expandir(fila.get(f"roca{i}")) for i in range(1, 5)]
    rocas = [r for r in rocas if r]

    return {
        "encontrado": True,
        "ambiente": limpiar(fila.get("ambiente")),
        "periodo": limpiar(fila.get("periodos")),
        "rocas_dominantes": rocas,
        "litoestratos": limpiar(fila.get("litoestratos")),
        "descripcion": limpiar(fila.get("litologia")),
    }


def consultar_fallas_cercanas(lat: float, lng: float, radio_km: float = 50) -> list:
    fallas_proj = fallas.to_crs("EPSG:32719")
    punto_proj = gpd.GeoSeries([Point(lng, lat)], crs="EPSG:4326").to_crs("EPSG:32719").iloc[0]

    fallas_proj = fallas_proj.copy()
    fallas_proj["distancia_km"] = fallas_proj.geometry.distance(punto_proj) / 1000

    cercanas = fallas_proj[fallas_proj["distancia_km"] <= radio_km].sort_values("distancia_km")

    return [
        {
            "nombre": limpiar(row.get("name")) or "Sin nombre catalogado",
            "distancia_km": round(row["distancia_km"], 1),
            "tipo_movimiento": limpiar(row.get("slip_type")),
        }
        for _, row in cercanas.iterrows()
    ]


def consultar_sismos_cercanos(
    lat: float,
    lng: float,
    radio_km: float = 100,
    min_magnitud: float = 4.0,
    profundidad_min: float = None,
    profundidad_max: float = None,
) -> list:
    df = sismos.copy()
    df = df.rename(columns={
        "Fecha (UTC)": "fecha",
        "Latitud [º]": "lat",
        "Longitud [º]": "lng",
        "Profundidad [km]": "profundidad_km",
        "Magnitud [*]": "magnitud",
    })

    R = 6371  # radio de la Tierra en km
    lat1, lon1 = np.radians(lat), np.radians(lng)
    lat2, lon2 = np.radians(df["lat"]), np.radians(df["lng"])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    df["distancia_km"] = R * 2 * np.arcsin(np.sqrt(a))

    cercanos = df[(df["distancia_km"] <= radio_km) & (df["magnitud"] >= min_magnitud)]

    if profundidad_min is not None:
        cercanos = cercanos[cercanos["profundidad_km"] >= profundidad_min]
    if profundidad_max is not None:
        cercanos = cercanos[cercanos["profundidad_km"] <= profundidad_max]

    cercanos = cercanos.sort_values("fecha", ascending=False)

    return [
        {
            "fecha": row["fecha"],
            "magnitud": limpiar(row["magnitud"]),
            "profundidad_km": limpiar(row["profundidad_km"]),
            "distancia_km": round(row["distancia_km"], 1),
        }
        for _, row in cercanos.iterrows()
    ]


# ---------------------------------------------------------------------------
# Modelo de riesgo: suma ponderada de factores normalizados (0-1), en vez de
# una escalera de condicionales con puntos fijos. Cada factor se transforma
# con una función de decaimiento/escala continua y se pondera según su
# importancia relativa. El resultado es un puntaje 0-100% y es completamente
# recalibrable cambiando estas constantes, sin tocar la lógica.
# ---------------------------------------------------------------------------

PROFUNDIDAD_CORTICAL_MAX_KM = 30  # separa sismos corticales de sismos de subducción
RADIO_INFLUENCIA_FALLA_KM = 30    # más allá de esto, una falla no aporta al puntaje

PESO_FALLA = 5
PESO_CORTICAL = 2
PESO_SUBDUCCION = 1
PESO_SUELO = 2
PESO_TOTAL = PESO_FALLA + PESO_CORTICAL + PESO_SUBDUCCION + PESO_SUELO

UMBRAL_ALTO_PCT = 60
UMBRAL_MODERADO_PCT = 30


def normalizar_distancia_falla(distancia_km: float) -> float:
    """1.0 si la falla está encima, decae linealmente a 0.0 en RADIO_INFLUENCIA_FALLA_KM."""
    return max(0.0, 1 - distancia_km / RADIO_INFLUENCIA_FALLA_KM)


def normalizar_magnitud(magnitud: float, base: float, techo: float = None) -> float:
    """0.0 en 'base', 1.0 en 'techo' (por defecto base+3), interpolado linealmente."""
    if techo is None:
        techo = base + 3.0
    return max(0.0, min(1.0, (magnitud - base) / (techo - base)))


def construir_factor(categoria: str, normalizado: float, peso_maximo: float, texto: str) -> dict:
    normalizado = round(normalizado, 3)
    return {
        "categoria": categoria,
        "normalizado": normalizado,       # 0-1, qué tan fuerte es este factor aquí
        "peso_maximo": peso_maximo,       # cuánto puede aportar como máximo al total
        "contribucion": round(normalizado * peso_maximo, 3),
        "texto": texto,
    }


def evaluar_riesgo(lat: float, lng: float) -> dict:
    geo_info = consultar_geologia(lat, lng)
    fallas_cercanas = consultar_fallas_cercanas(lat, lng, radio_km=50)

    sismos_corticales = consultar_sismos_cercanos(
        lat, lng, radio_km=RADIO_INFLUENCIA_FALLA_KM, min_magnitud=4.0,
        profundidad_max=PROFUNDIDAD_CORTICAL_MAX_KM,
    )
    sismos_subduccion = consultar_sismos_cercanos(
        lat, lng, radio_km=RADIO_INFLUENCIA_FALLA_KM, min_magnitud=4.5,
        profundidad_min=PROFUNDIDAD_CORTICAL_MAX_KM,
    )
    sismos_contexto = consultar_sismos_cercanos(lat, lng, radio_km=100, min_magnitud=4.0)

    factores = []

    # Factor: proximidad a la falla más cercana (decaimiento lineal por distancia)
    if fallas_cercanas:
        falla = fallas_cercanas[0]
        dist = falla["distancia_km"]
        s_falla = normalizar_distancia_falla(dist)
        texto = f"Falla '{falla['nombre']}' a {dist} km de distancia"
    else:
        s_falla = 0.0
        texto = "No se detectaron fallas catalogadas en el radio de 50 km"
    factores.append(construir_factor("falla", s_falla, PESO_FALLA, texto))

    # Factor: sismicidad cortical — la única atribuible a una falla superficial local
    if sismos_corticales:
        mayor = max(s["magnitud"] for s in sismos_corticales)
        s_cortical = normalizar_magnitud(mayor, base=4.0)
        texto = (
            f"{len(sismos_corticales)} sismo(s) corticales (prof. <{PROFUNDIDAD_CORTICAL_MAX_KM} km) "
            f"a menos de {RADIO_INFLUENCIA_FALLA_KM} km, M máx {mayor} — posible actividad de estructuras locales"
        )
    elif fallas_cercanas and fallas_cercanas[0]["distancia_km"] < 15:
        s_cortical = 0.0
        texto = (
            "Sin sismicidad cortical instrumental registrada pese a la falla cercana. "
            "Esto es consistente con el 'silencio sísmico' documentado en fallas como San Ramón: "
            "su peligrosidad se estableció mediante paleosismología (evidencia de rupturas antiguas "
            "en trincheras), no por sismos instrumentales recientes. Silencio no implica ausencia de riesgo."
        )
    else:
        s_cortical = 0.0
        texto = "Sin sismicidad cortical significativa registrada en el radio cercano"
    factores.append(construir_factor("sismicidad_cortical", s_cortical, PESO_CORTICAL, texto))

    # Factor: sismicidad de subducción — contextual, no atribuible a la falla cortical
    if sismos_subduccion:
        mayor_sub = max(s["magnitud"] for s in sismos_subduccion)
        s_sub = normalizar_magnitud(mayor_sub, base=4.5)
        texto = (
            f"{len(sismos_subduccion)} sismo(s) de subducción profundos (≥{PROFUNDIDAD_CORTICAL_MAX_KM} km) "
            f"cerca, M máx {mayor_sub} — corresponden a la interfaz de placas, no a la falla local"
        )
    else:
        s_sub = 0.0
        texto = "Sin sismos de subducción destacados en el radio cercano"
    factores.append(construir_factor("sismicidad_subduccion", s_sub, PESO_SUBDUCCION, texto))

    # Factor: tipo de suelo
    ambiente_raw = geo_info.get("ambiente") if geo_info.get("encontrado") else None
    if ambiente_raw:
        ambiente = ambiente_raw.lower()
        if "sedimentario" in ambiente and "continental" in ambiente:
            s_suelo = 1.0
            texto = "Suelo de depósitos sedimentarios/aluviales (mayor riesgo de amplificación sísmica y licuefacción)"
        elif "plutonico" in ambiente or "metamorfico" in ambiente:
            s_suelo = 0.0
            texto = "Roca consolidada (plutónica/metamórfica), generalmente más estable"
        else:
            s_suelo = 0.5
            texto = f"Suelo tipo: {ambiente_raw}"
    else:
        s_suelo = 0.5
        texto = "Sin clasificación de suelo disponible para este punto exacto"
    factores.append(construir_factor("suelo", s_suelo, PESO_SUELO, texto))

    contribucion_total = sum(f["contribucion"] for f in factores)
    score_pct = round(100 * contribucion_total / PESO_TOTAL, 1)

    if score_pct >= UMBRAL_ALTO_PCT:
        nivel = "Alto"
    elif score_pct >= UMBRAL_MODERADO_PCT:
        nivel = "Moderado"
    else:
        nivel = "Bajo"

    return {
        "nivel_riesgo": nivel,
        "score_pct": score_pct,
        "factores": factores,
        "modelo": {
            "pesos": {
                "falla": PESO_FALLA,
                "sismicidad_cortical": PESO_CORTICAL,
                "sismicidad_subduccion": PESO_SUBDUCCION,
                "suelo": PESO_SUELO,
            },
            "umbral_alto_pct": UMBRAL_ALTO_PCT,
            "umbral_moderado_pct": UMBRAL_MODERADO_PCT,
        },
        "datos_crudos": {
            "geologia": geo_info,
            "fallas_cercanas": fallas_cercanas,
            "sismos_corticales_cercanos": sismos_corticales,
            "sismos_subduccion_cercanos": sismos_subduccion,
            "sismos_contexto_regional": sismos_contexto[:5],
        },
    }


if __name__ == "__main__":
    import json

    puntos = {
        "Santiago centro": (-33.45, -70.65),
        "Antofagasta centro": (-23.65, -70.40),
        "San Pedro de Atacama": (-22.91, -68.20),
        "Piedemonte oriente (cerca Falla San Ramón)": (-33.45, -70.54),
    }

    for nombre, (lat, lng) in puntos.items():
        r = evaluar_riesgo(*puntos[nombre])
        print(f"\n--- {nombre} ---")
        print(f"Nivel: {r['nivel_riesgo']}  |  Score: {r['score_pct']}%")
        for f in r["factores"]:
            print(f"  [{f['categoria']}] normalizado={f['normalizado']} contribucion={f['contribucion']}/{f['peso_maximo']}")