import geopandas as gpd
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date
from shapely.geometry import Point

# Rutas resueltas según la ubicación de este archivo, no según el directorio
# desde el que se ejecute — así funciona igual con `python geo.py`,
# `pytest` desde cualquier carpeta, o un servidor de producción.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR.parent / "data"

geologia = gpd.read_file(DATA_DIR / "geologia" / "geologia.geojson")
fallas = gpd.read_file(DATA_DIR / "fallas" / "fallas_chile.geojson")
sismos = pd.read_csv(DATA_DIR / "sismos" / "sismos_csn.csv")
costa = gpd.read_file(DATA_DIR / "costa" / "costa_chile.geojson")

EXPANSION_ROCA = {
    "metareni": "metareniscas",
    "monzodio": "monzodiorita",
    "metapeli": "metapelitas",
    "metasedi": "metasedimentos",
    "granodio": "granodiorita",
}


def limpiar(valor):
    """Convierte cualquier variante de NaN/None/NA de pandas a None. Necesario
    porque columnas con datos faltantes pueden devolver distintos tipos de
    'vacío' (numpy.float64 nan, numpy.float32 nan, pandas.NA) según cómo se
    haya inferido el tipo de esa columna al leer el archivo — y NaN no es
    válido en JSON estricto (rompe la respuesta con 500 en producción)."""
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
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


def consultar_distancia_costa(lat: float, lng: float) -> float:
    """Distancia mínima (km) a la línea de costa — usada como aproximación de
    cercanía a la fosa de subducción, que corre mar adentro paralela a toda
    la costa chilena."""
    costa_proj = costa.to_crs("EPSG:32719")
    punto_proj = gpd.GeoSeries([Point(lng, lat)], crs="EPSG:4326").to_crs("EPSG:32719").iloc[0]
    distancias = costa_proj.geometry.distance(punto_proj) / 1000
    return float(distancias.min())


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

RADIO_EXPOSICION_COSTA_KM = 150   # decaimiento de exposición según distancia a la costa
RADIO_SUBDUCCION_GRANDE_KM = 200  # radio de búsqueda de grandes sismos de interfaz
MAGNITUD_BASE_SUBDUCCION_GRANDE = 6.0
MAGNITUD_TECHO_SUBDUCCION_GRANDE = 9.5  # Valdivia 1960, el mayor jamás registrado

UMBRAL_TSUNAMI_KM = 10  # bajo esta distancia, se agrega nota informativa (no puntaje)

# "Laguna sísmica": un segmento que rompió hace poco liberó tensión reciente.
# En vez de asumir un ciclo de recurrencia completo por segmento (que no
# conocemos con confianza para cada uno), usamos una ventana de alivio
# temporal post-ruptura más conservadora: durante ~30 años tras un gran
# sismo, tratamos el segmento como de urgencia algo menor; pasado eso,
# volvemos a evaluarlo por su exposición geográfica/histórica de base, sin
# extrapolar "más tiempo sin romper = más peligroso" sin evidencia específica
# del segmento — esa extrapolación sí requeriría el ciclo real, que no tenemos.
VENTANA_ALIVIO_POST_RUPTURA_ANIOS = 30

# El catálogo instrumental del CSN (evtdb) parte en 2012 — no incluye los
# grandes terremotos de subducción del siglo XX ni la década de 2010, que son
# precisamente los más relevantes para estimar exposición a la fosa. Esta
# tabla los complementa con los eventos mejor documentados de la sismología
# chilena (coordenadas aproximadas del epicentro/área de ruptura principal).
SISMOS_HISTORICOS_SUBDUCCION = [
    {"nombre": "Valdivia 1960", "fecha": "1960-05-22", "lat": -38.29, "lng": -73.05, "magnitud": 9.5},
    {"nombre": "Maule 2010", "fecha": "2010-02-27", "lat": -36.29, "lng": -73.24, "magnitud": 8.8},
    {"nombre": "Valparaíso 1985", "fecha": "1985-03-03", "lat": -33.24, "lng": -71.85, "magnitud": 8.0},
    {"nombre": "Iquique 2014", "fecha": "2014-04-01", "lat": -19.61, "lng": -70.77, "magnitud": 8.2},
    {"nombre": "Illapel 2015", "fecha": "2015-09-16", "lat": -31.57, "lng": -71.65, "magnitud": 8.3},
    {"nombre": "Antofagasta 1995", "fecha": "1995-07-30", "lat": -23.34, "lng": -70.29, "magnitud": 8.0},
]

PESO_FALLA = 5
PESO_CORTICAL = 2
PESO_EXPOSICION_SUBDUCCION = 4
PESO_SUELO = 2
PESO_TOTAL = PESO_FALLA + PESO_CORTICAL + PESO_EXPOSICION_SUBDUCCION + PESO_SUELO

UMBRAL_ALTO_PCT = 60
UMBRAL_MODERADO_PCT = 30

# Si UNA sola amenaza es extrema por sí sola, el punto es "Alto" sin importar
# el total ponderado — dos amenazas independientes no necesitan combinarse
# para justificar máxima cautela; basta con que la peor de ellas sea severa.
UMBRAL_FACTOR_EXTREMO = 0.85


def consultar_sismos_historicos_cercanos(lat: float, lng: float, radio_km: float = RADIO_SUBDUCCION_GRANDE_KM) -> list:
    """Busca en la tabla curada de grandes terremotos de subducción (no
    presentes en el catálogo instrumental del CSN, que solo cubre desde 2012)."""
    R = 6371
    lat1, lon1 = np.radians(lat), np.radians(lng)
    resultado = []
    for evento in SISMOS_HISTORICOS_SUBDUCCION:
        lat2, lon2 = np.radians(evento["lat"]), np.radians(evento["lng"])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
        distancia_km = R * 2 * np.arcsin(np.sqrt(a))
        if distancia_km <= radio_km:
            resultado.append({**evento, "distancia_km": round(float(distancia_km), 1)})
    return sorted(resultado, key=lambda e: e["distancia_km"])


def normalizar_distancia_falla(distancia_km: float) -> float:
    """1.0 si la falla está encima, decae linealmente a 0.0 en RADIO_INFLUENCIA_FALLA_KM."""
    return max(0.0, 1 - distancia_km / RADIO_INFLUENCIA_FALLA_KM)


def normalizar_exposicion_costa(distancia_km: float) -> float:
    """1.0 en la costa, decae linealmente a 0.0 en RADIO_EXPOSICION_COSTA_KM.
    Aproxima la exposición a la fosa de subducción, que corre mar adentro
    paralela a toda la costa chilena."""
    return max(0.0, 1 - distancia_km / RADIO_EXPOSICION_COSTA_KM)


def obtener_anio_evento(evento: dict) -> int:
    """Extrae el año de un evento, sea de la tabla histórica ('fecha': 'YYYY-MM-DD')
    o del catálogo instrumental ('fecha': 'YYYY-MM-DD HH:MM:SS')."""
    return int(evento["fecha"][:4])


def normalizar_laguna_sismica(anios_desde_ultimo_evento: float) -> float:
    """0.0 justo después de una ruptura (alivio temporal de tensión), 1.0 al
    cumplir o superar la ventana de alivio — de ahí en adelante se trata como
    'sin alivio reciente', sin seguir subiendo (no extrapolamos más allá)."""
    return max(0.0, min(1.0, anios_desde_ultimo_evento / VENTANA_ALIVIO_POST_RUPTURA_ANIOS))


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
    distancia_costa_km = round(consultar_distancia_costa(lat, lng), 1)

    sismos_corticales = consultar_sismos_cercanos(
        lat, lng, radio_km=RADIO_INFLUENCIA_FALLA_KM, min_magnitud=4.0,
        profundidad_max=PROFUNDIDAD_CORTICAL_MAX_KM,
    )
    # Radio amplio: un sismo de interfaz de subducción (como Valdivia 1960)
    # rompe cientos de km de costa a la vez, no es un evento "puntual" como
    # una falla cortical — buscar solo en 30km ignoraría su verdadero alcance.
    sismos_subduccion_grandes = consultar_sismos_cercanos(
        lat, lng, radio_km=RADIO_SUBDUCCION_GRANDE_KM, min_magnitud=MAGNITUD_BASE_SUBDUCCION_GRANDE,
        profundidad_min=PROFUNDIDAD_CORTICAL_MAX_KM,
    )
    # El catálogo instrumental (desde 2012) se complementa con la tabla
    # curada de grandes terremotos históricos — sin esto, Valdivia 1960 o
    # Maule 2010 simplemente no existirían para el modelo.
    sismos_historicos = consultar_sismos_historicos_cercanos(lat, lng)
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

    # Factor: exposición a la fosa de subducción — combina cercanía geográfica
    # a la costa (que existe sin importar si hubo un sismo grande "reciente":
    # los ciclos de recurrencia son de siglos, ver Valdivia 1960) con la
    # magnitud del mayor sismo de interfaz documentado en un radio amplio.
    s_costa = normalizar_exposicion_costa(distancia_costa_km)

    eventos_candidatos = sismos_subduccion_grandes + sismos_historicos

    if eventos_candidatos:
        mayor_sub = max(e["magnitud"] for e in eventos_candidatos)
        s_magnitud_sub = normalizar_magnitud(
            mayor_sub, base=MAGNITUD_BASE_SUBDUCCION_GRANDE, techo=MAGNITUD_TECHO_SUBDUCCION_GRANDE
        )
        fuente = "histórico" if sismos_historicos and mayor_sub == max(
            [e["magnitud"] for e in sismos_historicos], default=-1
        ) else "instrumental"

        anio_mas_reciente = max(obtener_anio_evento(e) for e in eventos_candidatos)
        anios_transcurridos = date.today().year - anio_mas_reciente
        s_laguna = normalizar_laguna_sismica(anios_transcurridos)
    else:
        mayor_sub = None
        s_magnitud_sub = 0.0
        fuente = None
        anios_transcurridos = None
        # Sin ningún evento de subducción documentado en el radio: no sabemos
        # si es porque genuinamente no hay actividad, o porque nuestra tabla
        # (6 eventos) y el catálogo (desde 2012) simplemente no lo cubren.
        # Neutral, no penalizamos ni premiamos una ausencia de datos.
        s_laguna = 0.5

    s_exposicion = 0.6 * s_costa + 0.2 * s_magnitud_sub + 0.2 * s_laguna
    texto = f"A {distancia_costa_km} km de la costa (proxy de la fosa de subducción)"
    if mayor_sub is not None:
        texto += f"; mayor sismo de interfaz en {RADIO_SUBDUCCION_GRANDE_KM} km: M{mayor_sub} (registro {fuente})"
        texto += (
            f"; {anios_transcurridos} años desde el evento más reciente documentado "
            f"(ventana de alivio post-ruptura {VENTANA_ALIVIO_POST_RUPTURA_ANIOS} años — "
            f"factor de laguna sísmica {s_laguna:.2f})"
        )
    else:
        texto += f"; sin sismos de interfaz M≥{MAGNITUD_BASE_SUBDUCCION_GRANDE} registrados en {RADIO_SUBDUCCION_GRANDE_KM} km"
    factores.append(construir_factor("exposicion_subduccion", s_exposicion, PESO_EXPOSICION_SUBDUCCION, texto))

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

    # Nota informativa (no afecta el puntaje): sin datos de elevación no podemos
    # calcular riesgo de inundación por tsunami con precisión — mejor ser
    # honestos sobre esa limitación que fabricar un número que parezca exacto.
    if distancia_costa_km < UMBRAL_TSUNAMI_KM:
        factores.append(construir_factor(
            "tsunami", 0.0, 0,
            f"Punto costero (a {distancia_costa_km} km del mar). Este modelo no calcula riesgo de "
            "inundación por tsunami porque requiere datos de elevación que no tenemos disponibles. "
            "Consulta las cartas oficiales de inundación por tsunami del SHOA para este sector."
        ))

    contribucion_total = sum(f["contribucion"] for f in factores)
    score_pct = round(100 * contribucion_total / PESO_TOTAL, 1)

    if score_pct >= UMBRAL_ALTO_PCT:
        nivel = "Alto"
    elif score_pct >= UMBRAL_MODERADO_PCT:
        nivel = "Moderado"
    else:
        nivel = "Bajo"

    # Anulación por amenaza extrema aislada: una falla casi encima, o una
    # exposición a subducción casi al máximo, ya justifican "Alto" por sí
    # solas — no deberían necesitar que otros factores también sumen para
    # que el resultado refleje el peligro real.
    factor_extremo = None
    if s_falla >= UMBRAL_FACTOR_EXTREMO:
        factor_extremo = "falla"
    elif s_exposicion >= UMBRAL_FACTOR_EXTREMO:
        factor_extremo = "exposicion_subduccion"

    if factor_extremo and nivel != "Alto":
        nivel = "Alto"
        factores.append(construir_factor(
            "anulacion_alto", 0.0, 0,
            f"Clasificado como Alto por amenaza extrema aislada en '{factor_extremo}' "
            f"(≥{UMBRAL_FACTOR_EXTREMO}), independientemente del puntaje total: una amenaza "
            "de esta magnitud no necesita combinarse con otras para justificar máxima cautela."
        ))

    return {
        "nivel_riesgo": nivel,
        "score_pct": score_pct,
        "factores": factores,
        "modelo": {
            "pesos": {
                "falla": PESO_FALLA,
                "sismicidad_cortical": PESO_CORTICAL,
                "exposicion_subduccion": PESO_EXPOSICION_SUBDUCCION,
                "suelo": PESO_SUELO,
            },
            "umbral_alto_pct": UMBRAL_ALTO_PCT,
            "umbral_moderado_pct": UMBRAL_MODERADO_PCT,
        },
        "datos_crudos": {
            "geologia": geo_info,
            "fallas_cercanas": fallas_cercanas,
            "sismos_corticales_cercanos": sismos_corticales,
            "distancia_costa_km": distancia_costa_km,
            "sismos_subduccion_grandes": sismos_subduccion_grandes,
            "sismos_historicos_cercanos": sismos_historicos,
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
        "Valdivia centro": (-39.8142, -73.2459),
    }

    for nombre, (lat, lng) in puntos.items():
        r = evaluar_riesgo(lat, lng)
        print(f"\n--- {nombre} ---")
        print(f"Nivel: {r['nivel_riesgo']}  |  Score: {r['score_pct']}%")
        for f in r["factores"]:
            print(f"  [{f['categoria']}] normalizado={f['normalizado']} contribucion={f['contribucion']}/{f['peso_maximo']}")