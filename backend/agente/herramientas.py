"""Herramientas (tools) que el agente puede llamar. Cada una envuelve una
función que ya existe en el proyecto y devuelve un dict JSON-serializable.

Criterios de diseño:
- Los errores se devuelven como {"error": ...} en vez de lanzar excepciones:
  así el modelo puede leer qué falló (p. ej. "fuera de Chile") y corregirse.
- Las respuestas se recortan a lo que el modelo necesita para explicar el
  resultado: cada token de una tool vuelve al contexto en cada turno."""
from functools import lru_cache

import requests
from langchain_core.tools import tool

import geo
from agente import rag

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# La política de uso de Nominatim exige identificar la aplicación.
USER_AGENT = "GeoRiesgoChile/1.0 (+https://github.com/ren2v/georiesgo-chile)"


@lru_cache(maxsize=256)
def _geocodificar(direccion: str) -> dict:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": direccion, "countrycodes": "cl", "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=5,
    )
    resp.raise_for_status()
    resultados = resp.json()
    if not resultados:
        return {"error": f"No se encontró la dirección '{direccion}' en Chile. Pide más detalle (comuna, ciudad)."}
    r = resultados[0]
    return {"lat": round(float(r["lat"]), 5), "lng": round(float(r["lon"]), 5), "direccion_encontrada": r["display_name"]}


@tool(parse_docstring=True)
def geocodificar_direccion(direccion: str) -> dict:
    """Convierte una dirección o lugar de Chile en coordenadas (lat, lng).

    Úsala antes de cualquier otra herramienta cuando el usuario da una
    dirección, comuna o lugar en vez de coordenadas. Revisa el campo
    direccion_encontrada: si no coincide con lo que pidió el usuario, díselo.

    Args:
        direccion: Dirección o lugar, idealmente con comuna y ciudad, p. ej. "Av. Providencia 1234, Providencia, Santiago".
    """
    try:
        return _geocodificar(direccion.strip())
    except requests.RequestException:
        return {"error": "El servicio de geocodificación no respondió. Pide al usuario coordenadas o reintenta."}


@tool(parse_docstring=True)
def evaluar_riesgo_sismico(lat: float, lng: float) -> dict:
    """Calcula el nivel de riesgo sísmico relativo (Bajo/Moderado/Alto) y el
    puntaje 0-100 % de un punto de Chile, con el detalle de cada factor.

    Args:
        lat: Latitud en grados decimales (negativa en Chile, entre -56 y -17).
        lng: Longitud en grados decimales (negativa en Chile, entre -76 y -66).
    """
    error = geo.error_coordenadas(lat, lng)
    if error:
        return {"error": error}
    r = geo.evaluar_riesgo(lat, lng)
    crudos = r["datos_crudos"]
    return {
        "nivel_riesgo": r["nivel_riesgo"],
        "score_pct": r["score_pct"],
        "factores": [
            {"categoria": f["categoria"], "contribucion": f["contribucion"], "peso_maximo": f["peso_maximo"], "texto": f["texto"]}
            for f in r["factores"]
        ],
        "geologia": crudos["geologia"],
        "distancia_costa_km": crudos["distancia_costa_km"],
        "fallas_mas_cercanas": crudos["fallas_cercanas"][:3],
        "sismos_historicos_cercanos": crudos["sismos_historicos_cercanos"],
    }


@tool(parse_docstring=True)
def buscar_sismos_cercanos(lat: float, lng: float, radio_km: float = 100, min_magnitud: float = 4.0) -> dict:
    """Lista los sismos registrados por el CSN (catálogo desde 2012) cerca de
    un punto, del más reciente al más antiguo.

    Args:
        lat: Latitud en grados decimales.
        lng: Longitud en grados decimales.
        radio_km: Radio de búsqueda en km (1 a 300).
        min_magnitud: Magnitud mínima a incluir (4.0 o más).
    """
    error = geo.error_coordenadas(lat, lng)
    if error:
        return {"error": error}
    radio_km = min(max(radio_km, 1), 300)
    sismos = geo.consultar_sismos_cercanos(lat, lng, radio_km=radio_km, min_magnitud=max(min_magnitud, 4.0))
    return {
        "total": len(sismos),
        "mayor_magnitud": max((s["magnitud"] for s in sismos), default=None),
        "mas_recientes": sismos[:10],
        "nota": "Catálogo instrumental del CSN desde 2012; no incluye terremotos anteriores.",
    }


@tool(parse_docstring=True)
def buscar_fallas_cercanas(lat: float, lng: float, radio_km: float = 50) -> dict:
    """Lista las fallas geológicas activas (base GEM) cerca de un punto,
    de la más cercana a la más lejana.

    Args:
        lat: Latitud en grados decimales.
        lng: Longitud en grados decimales.
        radio_km: Radio de búsqueda en km (1 a 200).
    """
    error = geo.error_coordenadas(lat, lng)
    if error:
        return {"error": error}
    fallas = geo.consultar_fallas_cercanas(lat, lng, radio_km=min(max(radio_km, 1), 200))
    return {"total": len(fallas), "fallas": fallas[:10]}


@tool(parse_docstring=True)
def buscar_en_documentos(pregunta: str) -> dict:
    """Busca en la base de conocimiento del proyecto: metodología del modelo
    de riesgo, fuentes de datos, recomendaciones oficiales de SENAPRED ante
    sismos y tsunamis, cartas de inundación del SHOA, norma sísmica NCh433 y
    conceptos de sismología. Úsala para explicar el porqué de un resultado o
    para preguntas generales, y cita el título y la url de lo que uses.

    Args:
        pregunta: Qué buscar, formulado como pregunta o tema, p. ej. "cómo se calcula el factor de subducción".
    """
    return {"resultados": rag.buscar(pregunta, k=5)}


HERRAMIENTAS = [
    geocodificar_direccion,
    evaluar_riesgo_sismico,
    buscar_sismos_cercanos,
    buscar_fallas_cercanas,
    buscar_en_documentos,
]
