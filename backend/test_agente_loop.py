"""Tests del loop del agente y del endpoint /agente con un modelo falso:
verifican el cableado (herramientas reales, memoria, eventos SSE) sin
llamar a ningún LLM ni gastar cuota."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agente import agente
import main


class ModeloFalso(GenericFakeChatModel):
    """Devuelve los mensajes dados, en orden. create_agent exige bind_tools;
    el modelo falso no necesita las herramientas porque sus llamadas ya
    vienen escritas en los mensajes."""

    def bind_tools(self, tools, **kwargs):
        return self


def llamada(nombre: str, args: dict, id_: str = "llamada-1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": nombre, "args": args, "id": id_}])


@pytest.fixture
def agente_con_modelo(monkeypatch):
    """Arma el agente real (herramientas, prompt, middleware, memoria) sobre
    un modelo falso que responde los mensajes que le pase cada test."""
    def armar(*mensajes: AIMessage):
        monkeypatch.setattr(agente, "init_chat_model", lambda *a, **k: ModeloFalso(messages=iter(mensajes)))
        agente.obtener_agente.cache_clear()
        return agente
    yield armar
    agente.obtener_agente.cache_clear()


def test_agente_ejecuta_la_herramienta_y_devuelve_la_respuesta_final(agente_con_modelo):
    ag = agente_con_modelo(
        llamada("evaluar_riesgo_sismico", {"lat": -33.45, "lng": -70.65}),
        AIMessage(content="Santiago centro tiene riesgo relativo moderado."),
    )
    resultado = ag.preguntar("¿qué riesgo tiene Santiago centro?", "conv-1")

    assert resultado["respuesta"] == "Santiago centro tiene riesgo relativo moderado."
    [herramienta] = resultado["herramientas"]
    assert herramienta["nombre"] == "evaluar_riesgo_sismico"
    assert herramienta["args"] == {"lat": -33.45, "lng": -70.65}
    # La herramienta corrió de verdad y su resultado viene parseado.
    assert herramienta["resultado"]["nivel_riesgo"] in {"Bajo", "Moderado", "Alto"}


def test_agente_recuerda_la_conversacion_y_reporta_solo_el_turno_actual(agente_con_modelo):
    ag = agente_con_modelo(
        llamada("evaluar_riesgo_sismico", {"lat": -33.45, "lng": -70.65}),
        AIMessage(content="Primera respuesta."),
        AIMessage(content="Segunda respuesta, sin herramientas."),
    )
    ag.preguntar("primera pregunta", "conv-2")
    segunda = ag.preguntar("¿y eso qué significa?", "conv-2")

    assert segunda["respuesta"] == "Segunda respuesta, sin herramientas."
    assert segunda["herramientas"] == []
    estado = ag.obtener_agente().get_state({"configurable": {"thread_id": "conv-2"}})
    assert [m.type for m in estado.values["messages"]] == ["human", "ai", "tool", "ai", "human", "ai"]


def test_agente_se_detiene_al_llegar_al_tope_de_llamadas(agente_con_modelo):
    # Un modelo que nunca deja de pedir herramientas no debe consumir cuota
    # sin fin: el middleware corta en MAX_LLAMADAS_MODELO.
    en_loop = [llamada("buscar_fallas_cercanas", {"lat": -33.45, "lng": -70.54}, f"l{i}") for i in range(20)]
    ag = agente_con_modelo(*en_loop)
    ag.preguntar("pregunta que entra en loop", "conv-3")

    estado = ag.obtener_agente().get_state({"configurable": {"thread_id": "conv-3"}})
    llamadas_al_modelo = [m for m in estado.values["messages"] if m.type == "ai" and m.tool_calls]
    assert len(llamadas_al_modelo) == agente.MAX_LLAMADAS_MODELO


@pytest.mark.parametrize("mensaje, reintentar", [
    ("503 UNAVAILABLE. This model is currently experiencing high demand.", True),
    ("429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier", True),
    # Cuota diaria: no vuelve en segundos, se pasa al modelo de respaldo.
    ("429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", False),
    ("400 INVALID_ARGUMENT. API key not valid.", False),
    ("404 NOT_FOUND. This model is no longer available.", False),
])
def test_solo_se_reintentan_errores_transitorios(mensaje, reintentar):
    assert agente.es_error_transitorio(RuntimeError(mensaje)) is reintentar


def test_modelo_principal_caido_pasa_al_respaldo(monkeypatch):
    class ModeloCaido(ModeloFalso):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")

    def fabrica(nombre, **kwargs):
        if nombre == agente.MODELO:
            return ModeloCaido(messages=iter([]))
        return ModeloFalso(messages=iter([AIMessage(content=f"Respondió {nombre}.")]))

    monkeypatch.setattr(agente, "init_chat_model", fabrica)
    # Mismo middleware de reintentos, pero sin esperar entre intentos.
    reintentos_reales = agente.ModelRetryMiddleware
    monkeypatch.setattr(agente, "ModelRetryMiddleware",
                        lambda **kw: reintentos_reales(**{**kw, "initial_delay": 0, "jitter": False}))
    agente.obtener_agente.cache_clear()
    try:
        resultado = agente.preguntar("hola", "conv-respaldo")
    finally:
        agente.obtener_agente.cache_clear()
    assert resultado["respuesta"] == f"Respondió {agente.MODELOS_RESPALDO[0]}."


# --- Endpoint /agente ---

cliente = TestClient(main.app)


def leer_eventos(respuesta) -> list[dict]:
    return [json.loads(linea.removeprefix("data: ")) for linea in respuesta.text.splitlines() if linea.startswith("data: ")]


def test_endpoint_sin_llave_devuelve_503():
    with patch.object(agente, "llave_configurada", return_value=False):
        r = cliente.post("/agente", json={"mensaje": "hola"})
    assert r.status_code == 503


@pytest.mark.parametrize("cuerpo", [{"mensaje": ""}, {"mensaje": "x" * 1001}, {}])
def test_endpoint_valida_el_mensaje(cuerpo):
    assert cliente.post("/agente", json=cuerpo).status_code == 422


def test_endpoint_emite_eventos_sse_con_id_de_conversacion():
    async def en_vivo(mensaje, conversacion_id):
        yield {"tipo": "herramienta", "nombre": "evaluar_riesgo_sismico", "args": {"lat": -33.45, "lng": -70.65}}
        yield {"tipo": "texto", "texto": "Riesgo moderado."}
        yield {"tipo": "fin"}

    with patch.object(agente, "llave_configurada", return_value=True), \
         patch.object(agente, "preguntar_en_vivo", en_vivo):
        r = cliente.post("/agente", json={"mensaje": "¿Santiago?", "conversacion_id": "abc"})

    assert r.headers["content-type"].startswith("text/event-stream")
    eventos = leer_eventos(r)
    assert eventos[0] == {"tipo": "inicio", "conversacion_id": "abc"}
    assert [e["tipo"] for e in eventos[1:]] == ["herramienta", "texto", "fin"]


def test_endpoint_convierte_limite_de_cuota_en_evento_de_error_legible():
    async def en_vivo(mensaje, conversacion_id):
        raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        yield  # hace de esto un generador async

    with patch.object(agente, "llave_configurada", return_value=True), \
         patch.object(agente, "preguntar_en_vivo", en_vivo):
        eventos = leer_eventos(cliente.post("/agente", json={"mensaje": "hola"}))

    assert eventos[-1]["tipo"] == "error"
    assert "límite de uso gratuito" in eventos[-1]["detalle"]
