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
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

from agente.herramientas import HERRAMIENTAS

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

MODELO = os.getenv("GEORIESGO_MODELO", "google_genai:gemini-3.8-flash")

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


@lru_cache(maxsize=1)
def obtener_agente():
    return create_agent(
        init_chat_model(MODELO, temperature=0),
        HERRAMIENTAS,
        system_prompt=PROMPT_SISTEMA,
        middleware=[
            ModelCallLimitMiddleware(run_limit=MAX_LLAMADAS_MODELO),
            ToolCallLimitMiddleware(run_limit=MAX_LLAMADAS_HERRAMIENTAS),
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
