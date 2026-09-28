"""Agente conversacional de GeoRiesgo Chile: un LLM que responde preguntas
sobre riesgo sísmico llamando a las herramientas del proyecto (consultas
espaciales + búsqueda en documentos) en vez de responder de memoria.

El modelo se elige con GEORIESGO_MODELO en formato "proveedor:modelo" de
LangChain, p. ej. "google_genai:gemini-3.8-flash" o
"anthropic:claude-opus-5": cambiar de proveedor no toca este código."""
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

from agente.herramientas import HERRAMIENTAS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MODELO = os.getenv("GEORIESGO_MODELO", "google_genai:gemini-3.6-flash")

# Si el modelo principal falla tras los reintentos, se prueba con estos, en
# orden (lista separada por comas). En el tier gratuito de Gemini la cuota
# diaria es por modelo (p. ej. 20 peticiones/día en gemini-3.8-flash) y los
# modelos nuevos suelen estar saturados, así que una cadena larga es lo que
# hace usable el agente.
MODELOS_RESPALDO_POR_DEFECTO = ",".join(f"google_genai:{m}" for m in [
    "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
])
MODELOS_RESPALDO = [
    m.strip()
    for m in os.getenv("GEORIESGO_MODELOS_RESPALDO", MODELOS_RESPALDO_POR_DEFECTO).split(",")
    if m.strip()
]

# Variable de entorno con la API key que exige cada proveedor.
LLAVES_POR_PROVEEDOR = {
    "google_genai": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Topes por pregunta: protegen la cuota del tier gratuito (y el bolsillo en
# uno pagado) si el modelo entra en un loop de llamadas. Una pregunta normal
# usa 2-4 llamadas al modelo.
MAX_LLAMADAS_MODELO = 6
MAX_LLAMADAS_HERRAMIENTAS = 10

PROMPT_SISTEMA = """\
Eres el asistente de GeoRiesgo Chile, una herramienta que estima el riesgo sísmico relativo de un punto de Chile a partir de datos públicos (SERNAGEOMIN, CSN, GEM, SHOA).

Cómo trabajas:
- Cualquier dato sobre un lugar (nivel de riesgo, fallas, sismos, suelo, distancia a la costa, tsunami) sale SIEMPRE de tus herramientas. Nunca inventes ni estimes cifras de memoria.
- Si el usuario da una dirección, comuna o lugar, usa primero geocodificar_direccion. Si la dirección encontrada no coincide con lo que pidió, díselo y pide más detalle.
- Para explicar por qué un resultado es así, o para preguntas generales (qué hacer en un sismo, qué es una carta CITSU, cómo se calcula el puntaje), usa buscar_en_documentos y menciona la fuente (título y url) de lo que uses.
- Si una herramienta devuelve un error, explícalo en simple y propone cómo seguir.

Cómo respondes:
- En español de Chile, claro y breve: primero la respuesta directa, después el detalle que importa. Usa listas cortas si ayudan.
- El puntaje es una comparación relativa entre puntos de Chile, no una probabilidad de terremoto: todo Chile es sísmico. Dilo cuando presentes un nivel de riesgo.
- Nadie puede predecir cuándo ocurrirá un terremoto; no lo insinúes.
- No reemplazas a un ingeniero, geólogo ni estudio de mecánica de suelos: recomiéndalos para decisiones de construcción o compra.
- Si alguien describe una emergencia en curso, indícale seguir las instrucciones de SENAPRED y las autoridades, y evacuar si está en la costa tras un sismo fuerte.
- Si la pregunta no tiene que ver con riesgo sísmico, tsunamis o geología de Chile, dilo amablemente y ofrece en qué puedes ayudar.
"""


def llave_configurada() -> bool:
    proveedor = MODELO.split(":", 1)[0]
    variable = LLAVES_POR_PROVEEDOR.get(proveedor)
    return variable is None or bool(os.getenv(variable))


def es_error_transitorio(error: Exception) -> bool:
    """Errores que vale la pena reintentar con el mismo modelo: saturado
    (503), error interno (500) o límite por minuto (429). No se reintentan
    una key inválida (400/403) ni la cuota diaria agotada: esa no vuelve en
    segundos, así que conviene pasar directo al modelo de respaldo. Se lee
    el texto del error para no depender de las clases de cada proveedor."""
    texto = str(error)
    if "PerDay" in texto:
        return False
    return any(marca in texto for marca in ("503", "500", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overloaded"))


def _modelo(nombre: str):
    # Sin reintentos internos del cliente (el de Gemini trae 6 por defecto y
    # sin timeout): los reintentos los maneja ModelRetryMiddleware, una sola
    # capa visible. Si no, se multiplican por cada modelo de respaldo.
    return init_chat_model(nombre, temperature=0, max_retries=0, timeout=60)


@lru_cache(maxsize=1)
def obtener_agente():
    return create_agent(
        _modelo(MODELO),
        HERRAMIENTAS,
        system_prompt=PROMPT_SISTEMA,
        middleware=[
            ModelCallLimitMiddleware(run_limit=MAX_LLAMADAS_MODELO),
            ToolCallLimitMiddleware(run_limit=MAX_LLAMADAS_HERRAMIENTAS),
            # El respaldo envuelve a los reintentos: cada modelo se reintenta
            # antes de pasar al siguiente.
            *([ModelFallbackMiddleware(*[_modelo(m) for m in MODELOS_RESPALDO])] if MODELOS_RESPALDO else []),
            # on_failure="error": si se agotan los reintentos, que falle de
            # verdad. El valor por defecto devuelve el error disfrazado de
            # respuesta del modelo, y eso lo esconde del usuario y de las evals.
            ModelRetryMiddleware(max_retries=2, retry_on=es_error_transitorio, on_failure="error", initial_delay=2.0),
        ],
        # Memoria de conversación por conversacion_id, en memoria del proceso:
        # se pierde al reiniciar. En producción: checkpointer de Postgres.
        checkpointer=InMemorySaver(),
    )


def _config(conversacion_id: str) -> dict:
    return {"configurable": {"thread_id": conversacion_id}}


def preguntar(mensaje: str, conversacion_id: str) -> dict:
    """Versión sin streaming: devuelve la respuesta final y las herramientas
    que se usaron, con su resultado (las evals lo usan para verificar que la
    respuesta sea consistente con los datos). La usa también la consola."""
    estado = obtener_agente().invoke({"messages": [{"role": "user", "content": mensaje}]}, _config(conversacion_id))
    mensajes = estado["messages"]
    # Solo los mensajes de este turno (desde el último mensaje del usuario).
    inicio = max(i for i, m in enumerate(mensajes) if m.type == "human")
    turno = mensajes[inicio + 1:]
    resultados = {m.tool_call_id: m.content for m in turno if m.type == "tool"}
    return {
        "respuesta": turno[-1].text,
        # Qué modelo respondió de verdad (puede ser uno de respaldo).
        "modelo": turno[-1].response_metadata.get("model_name"),
        "herramientas": [
            {"nombre": llamada["name"], "args": llamada["args"], "resultado": _leer_json(resultados.get(llamada["id"]))}
            for m in turno if m.type == "ai"
            for llamada in m.tool_calls
        ],
    }


def _leer_json(contenido):
    try:
        return json.loads(contenido)
    except (TypeError, ValueError):
        return contenido


async def preguntar_en_vivo(mensaje: str, conversacion_id: str) -> AsyncIterator[dict]:
    """Emite eventos mientras el agente trabaja:
    {"tipo": "herramienta", "nombre", "args"} cuando decide llamar una herramienta,
    {"tipo": "texto", "texto"} por cada fragmento de la respuesta final,
    {"tipo": "fin"} al terminar."""
    entrada = {"messages": [{"role": "user", "content": mensaje}]}
    async for modo, datos in obtener_agente().astream(
        entrada, _config(conversacion_id), stream_mode=["messages", "updates"]
    ):
        if modo == "messages":
            fragmento, meta = datos
            # Solo texto generado por el modelo (no el contenido de las
            # herramientas, que también pasa por este canal).
            if meta.get("langgraph_node") == "model" and fragmento.type == "AIMessageChunk" and fragmento.text:
                yield {"tipo": "texto", "texto": fragmento.text}
        elif modo == "updates":
            for actualizacion in datos.values():
                for m in (actualizacion or {}).get("messages", []):
                    for llamada in getattr(m, "tool_calls", None) or []:
                        yield {"tipo": "herramienta", "nombre": llamada["name"], "args": llamada["args"]}
    yield {"tipo": "fin"}
