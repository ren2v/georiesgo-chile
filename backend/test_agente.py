"""Tests de las piezas del agente que no dependen del LLM: herramientas y RAG."""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from agente import herramientas, rag

SANTIAGO = {"lat": -33.45, "lng": -70.65}
VALPARAISO = {"lat": -33.04, "lng": -71.62}
FUERA_DE_CHILE = {"lat": -34.6, "lng": -58.4}  # Buenos Aires


def es_json(valor) -> bool:
    """El resultado de una herramienta vuelve al LLM como JSON: un
    numpy.float64 o un NaN colado rompería la llamada."""
    json.dumps(valor, allow_nan=False)
    return True


# --- Definición de las herramientas ---

@pytest.mark.parametrize("herramienta", herramientas.HERRAMIENTAS, ids=lambda h: h.name)
def test_herramienta_documenta_todos_sus_argumentos(herramienta):
    # El modelo solo ve el nombre, la descripción y el esquema: un argumento
    # sin descripción es un argumento que el modelo tiene que adivinar.
    assert herramienta.description
    for nombre, esquema in herramienta.args.items():
        assert esquema.get("description"), f"{herramienta.name}.{nombre} sin descripción"


# --- evaluar_riesgo_sismico ---

@pytest.mark.parametrize("punto", [SANTIAGO, VALPARAISO])
def test_evaluar_riesgo_devuelve_nivel_y_factores(punto):
    r = herramientas.evaluar_riesgo_sismico.invoke(punto)
    assert r["nivel_riesgo"] in {"Bajo", "Moderado", "Alto"}
    assert 0 <= r["score_pct"] <= 100
    assert {f["categoria"] for f in r["factores"]} >= {"falla", "sismicidad_cortical", "exposicion_subduccion", "suelo"}
    assert es_json(r)


def test_evaluar_riesgo_fuera_de_chile_devuelve_error_no_excepcion():
    r = herramientas.evaluar_riesgo_sismico.invoke(FUERA_DE_CHILE)
    assert "error" in r


# --- sismos y fallas ---

def test_buscar_sismos_limita_radio_y_resultados():
    r = herramientas.buscar_sismos_cercanos.invoke({**VALPARAISO, "radio_km": 10_000, "min_magnitud": 4.0})
    assert len(r["mas_recientes"]) <= 10
    assert r["total"] >= len(r["mas_recientes"])
    assert all(s["distancia_km"] <= 300 for s in r["mas_recientes"])
    assert es_json(r)


def test_buscar_fallas_cercanas_ordenadas_por_distancia():
    r = herramientas.buscar_fallas_cercanas.invoke({"lat": -33.45, "lng": -70.54, "radio_km": 50})
    distancias = [f["distancia_km"] for f in r["fallas"]]
    assert distancias == sorted(distancias)
    assert es_json(r)


@pytest.mark.parametrize("herramienta", [herramientas.buscar_sismos_cercanos, herramientas.buscar_fallas_cercanas])
def test_busquedas_fuera_de_chile_devuelven_error(herramienta):
    assert "error" in herramienta.invoke(FUERA_DE_CHILE)


# --- geocodificar_direccion (sin red: requests.get simulado) ---

@pytest.fixture(autouse=True)
def limpiar_cache_geocodificacion():
    herramientas._geocodificar.cache_clear()


def respuesta_nominatim(resultados):
    resp = MagicMock()
    resp.json.return_value = resultados
    return resp


def test_geocodificar_devuelve_coordenadas_y_direccion():
    nominatim = [{"lat": "-33.4378", "lon": "-70.6504", "display_name": "Plaza de Armas, Santiago, Chile"}]
    with patch("agente.herramientas.requests.get", return_value=respuesta_nominatim(nominatim)) as get:
        r = herramientas.geocodificar_direccion.invoke({"direccion": "Plaza de Armas, Santiago"})
    assert r == {"lat": -33.4378, "lng": -70.6504, "direccion_encontrada": "Plaza de Armas, Santiago, Chile"}
    parametros = get.call_args.kwargs
    assert parametros["params"]["countrycodes"] == "cl"
    assert "GeoRiesgoChile" in parametros["headers"]["User-Agent"]


def test_geocodificar_sin_resultados_devuelve_error():
    with patch("agente.herramientas.requests.get", return_value=respuesta_nominatim([])):
        r = herramientas.geocodificar_direccion.invoke({"direccion": "Calle Inventada 999"})
    assert "error" in r


def test_geocodificar_con_servicio_caido_devuelve_error():
    with patch("agente.herramientas.requests.get", side_effect=requests.ConnectionError):
        r = herramientas.geocodificar_direccion.invoke({"direccion": "Valparaíso"})
    assert "error" in r


# --- RAG: división de documentos (sin base de datos) ---

DOCUMENTOS = sorted(rag.CARPETA_CONOCIMIENTO.glob("*.md"))


def test_hay_documentos_en_la_base_de_conocimiento():
    assert len(DOCUMENTOS) >= 5


@pytest.mark.parametrize("ruta", DOCUMENTOS, ids=lambda r: r.name)
def test_cada_fragmento_lleva_titulo_y_fuente(ruta):
    fragmentos = rag.dividir_documento(ruta)
    assert fragmentos
    for f in fragmentos:
        assert f.page_content.startswith("Documento: ")
        assert f.metadata["titulo"] and f.metadata["fuente"] and f.metadata["seccion"]
        assert f.metadata["archivo"] == ruta.name


# --- RAG: recuperación (requiere pgvector cargado con backend/agente/ingesta.py) ---

def _rag_disponible() -> bool:
    try:
        return bool(rag.buscar("sismo", k=1))
    except Exception:
        return False


requiere_rag = pytest.mark.skipif(not _rag_disponible(), reason="pgvector no disponible o sin documentos indexados")


@requiere_rag
@pytest.mark.parametrize("pregunta, archivos_esperados", [
    ("¿qué hago si tiembla fuerte y estoy en la playa?", {"senapred_tsunami.md", "senapred_sismos.md"}),
    ("¿qué significa que mi casa esté dentro de una carta CITSU?", {"shoa_citsu.md"}),
    ("¿por qué la falla de San Ramón es peligrosa si casi no tiembla?", {"conceptos_sismologia.md", "metodologia_modelo.md"}),
    ("¿qué tipo de suelo amplifica más las ondas sísmicas?", {"norma_sismica_nch433.md"}),
    ("¿de dónde salen los datos de sismos?", {"fuentes_datos.md"}),
])
def test_rag_recupera_el_documento_relevante(pregunta, archivos_esperados):
    # Regresión de calidad de búsqueda: el documento correcto debe estar
    # entre los 3 primeros. Si esto falla tras cambiar el modelo de
    # embeddings o el troceo, la búsqueda empeoró.
    titulos = {r["titulo"] for r in rag.buscar(pregunta, k=3)}
    titulos_esperados = {rag.leer_documento(rag.CARPETA_CONOCIMIENTO / a)[0]["titulo"] for a in archivos_esperados}
    assert titulos & titulos_esperados
