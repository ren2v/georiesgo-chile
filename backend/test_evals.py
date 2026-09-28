"""Tests de las verificaciones de las evals (sin LLM): si un check está mal
escrito, el puntaje de las evals miente."""
import pytest

from agente.evaluar import nombrados, verificar


@pytest.mark.parametrize("texto, esperado", [
    ("Cerro Alegre tiene un riesgo sísmico relativo **Alto**.", {"Alto"}),
    ("El nivel de riesgo es Moderado (45 %).", {"Moderado"}),
    ("Nivel: **Bajo**", {"Bajo"}),
    ("La zona tiene riesgo alto.", {"Alto"}),
    # Respuesta real de Gemini que el patrón anterior no detectaba:
    ("El nivel de riesgo sísmico relativo en la **Plaza de Armas de Santiago** es **Moderado** (puntaje del **56.8%**).",
     {"Moderado"}),
    # Falsos positivos que el patrón debe ignorar:
    ("La placa de Nazca se hunde bajo la Sudamericana.", set()),
    ("Evacúa hacia terreno alto.", set()),
    ("El riesgo de tsunami baja si evacúas a un terreno alto.", set()),
    ("", set()),
])
def test_nombrados_detecta_solo_niveles_atribuidos(texto, esperado):
    assert nombrados(texto) == esperado


def evaluacion(nivel):
    return {"nombre": "evaluar_riesgo_sismico", "args": {}, "resultado": {"nivel_riesgo": nivel}}


def checks_por_nombre(caso, resultado):
    return {c["check"]: c["ok"] for c in verificar(caso, resultado)}


def test_nivel_consistente_falla_si_la_respuesta_contradice_la_herramienta():
    caso = {"nivel_consistente": True}
    ok = {"respuesta": "Tiene riesgo Alto, relativo al resto de Chile.", "herramientas": [evaluacion("Alto")]}
    contradice = {"respuesta": "Tiene riesgo Bajo.", "herramientas": [evaluacion("Alto")]}
    sin_evaluar = {"respuesta": "Tiene riesgo Alto.", "herramientas": []}
    assert checks_por_nombre(caso, ok)["nivel_consistente"]
    assert not checks_por_nombre(caso, contradice)["nivel_consistente"]
    # Si no llamó a la herramienta, el nivel lo inventó.
    assert not checks_por_nombre(caso, sin_evaluar)["nivel_consistente"]


def test_herramientas_esperadas_exige_el_orden_pero_permite_llamadas_extra():
    caso = {"herramientas_esperadas": ["geocodificar_direccion", "evaluar_riesgo_sismico"]}
    usar = lambda *nombres: {"respuesta": "", "herramientas": [{"nombre": n, "args": {}, "resultado": None} for n in nombres]}
    assert checks_por_nombre(caso, usar("geocodificar_direccion", "buscar_en_documentos", "evaluar_riesgo_sismico"))["herramientas_esperadas"]
    assert not checks_por_nombre(caso, usar("evaluar_riesgo_sismico", "geocodificar_direccion"))["herramientas_esperadas"]


def test_debe_mencionar_ignora_tildes_y_mayusculas():
    caso = {"debe_mencionar": [["San Ramón"]], "no_debe_mencionar": ["messi"]}
    checks = checks_por_nombre(caso, {"respuesta": "La falla de SAN RAMON... y Messi.", "herramientas": []})
    assert checks["menciona:San Ramón"]
    assert not checks["no_menciona:messi"]


def test_debe_mencionar_basta_una_alternativa_por_grupo():
    caso = {"debe_mencionar": [["evacu"], ["altura", "zona segura"]]}
    assert all(checks_por_nombre(caso, {"respuesta": "Evacúa a una zona segura.", "herramientas": []}).values())
    assert not all(checks_por_nombre(caso, {"respuesta": "Evacúa ya.", "herramientas": []}).values())
