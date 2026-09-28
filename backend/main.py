import json
import logging
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import requests
import geo

logger = logging.getLogger("georiesgo")

app = FastAPI(title="GeoRiesgo Chile API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def validar_coordenadas(lat: float, lng: float):
    error = geo.error_coordenadas(lat, lng)
    if error:
        raise HTTPException(status_code=400, detail=error)


def consultar_elevacion(lat: float, lng: float):
    """Consulta la API pública Open-Elevation (gratuita, sin llave). A
    diferencia de los demás datos del proyecto, esta es una dependencia
    externa consultada en cada petición, no cargada una sola vez al iniciar
    — por eso vive aquí (capa HTTP) y no en geo.py (lógica pura, testeable
    sin red). Timeout corto y degradación elegante: cualquier falla devuelve
    None en vez de romper la respuesta completa."""
    try:
        resp = requests.get(
            "https://api.open-elevation.com/api/v1/lookup",
            params={"locations": f"{lat},{lng}"},
            timeout=3,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data["results"][0]["elevation"])
    except Exception:
        return None


@app.get("/")
def raiz():
    return {"mensaje": "GeoRiesgo Chile API", "backend_datos": geo.BACKEND, "endpoints": ["/geologia", "/fallas", "/sismos", "/consulta", "/riesgo", "/agente"]}


@app.get("/geologia")
def geologia(lat: float, lng: float):
    validar_coordenadas(lat, lng)
    return geo.consultar_geologia(lat, lng)


@app.get("/fallas")
def fallas(lat: float, lng: float, radio_km: float = 50):
    validar_coordenadas(lat, lng)
    return geo.consultar_fallas_cercanas(lat, lng, radio_km)


@app.get("/sismos")
def sismos(lat: float, lng: float, radio_km: float = 100, min_magnitud: float = 4.0):
    validar_coordenadas(lat, lng)
    return geo.consultar_sismos_cercanos(lat, lng, radio_km, min_magnitud)


@app.get("/consulta")
def consulta_completa(lat: float, lng: float):
    validar_coordenadas(lat, lng)
    return {
        "coordenadas": {"lat": lat, "lng": lng},
        "geologia": geo.consultar_geologia(lat, lng),
        "fallas_cercanas": geo.consultar_fallas_cercanas(lat, lng),
        "sismos_cercanos": geo.consultar_sismos_cercanos(lat, lng)[:10],
    }


@app.get("/riesgo")
def riesgo(lat: float, lng: float):
    validar_coordenadas(lat, lng)
    resultado = geo.evaluar_riesgo(lat, lng)

    # Elevación: solo se consulta para puntos costeros (donde ya se muestra
    # la nota de tsunami) — evita llamadas externas innecesarias en el resto
    # de los casos, y su ausencia nunca rompe la respuesta.
    distancia_costa_km = resultado["datos_crudos"]["distancia_costa_km"]
    if distancia_costa_km < geo.UMBRAL_TSUNAMI_KM:
        elevacion = consultar_elevacion(lat, lng)
        resultado["elevacion_m"] = elevacion
        if elevacion is not None:
            for factor in resultado["factores"]:
                if factor["categoria"] == "tsunami":
                    factor["texto"] += f" Elevación aproximada del punto: {elevacion:.0f} m sobre el nivel del mar."
    else:
        resultado["elevacion_m"] = None

    return resultado


class PreguntaAgente(BaseModel):
    mensaje: str = Field(min_length=1, max_length=1000)
    # Mismo id en varias preguntas = misma conversación (el agente recuerda
    # lo anterior). Si no viene, se crea una nueva y se devuelve en el evento
    # "inicio" para que el cliente la reutilice.
    conversacion_id: str | None = Field(default=None, max_length=64)


@app.post("/agente")
async def preguntar_al_agente(pregunta: PreguntaAgente):
    """Responde en streaming (Server-Sent Events): un evento por herramienta
    que el agente decide usar y por cada fragmento de texto de la respuesta."""
    # Importación diferida: LangChain y el modelo de embeddings solo se
    # cargan si alguien usa el agente; el resto de la API arranca igual.
    from agente import agente

    if not agente.llave_configurada():
        raise HTTPException(status_code=503, detail="El agente no está configurado (falta la API key del modelo).")

    conversacion_id = pregunta.conversacion_id or str(uuid.uuid4())

    async def eventos():
        yield _sse({"tipo": "inicio", "conversacion_id": conversacion_id})
        try:
            async for evento in agente.preguntar_en_vivo(pregunta.mensaje, conversacion_id):
                yield _sse(evento)
        except Exception as e:
            # Con el stream ya abierto no se puede cambiar el código HTTP:
            # el error viaja como un evento más.
            logger.exception("Error del agente en la conversación %s", conversacion_id)
            yield _sse({"tipo": "error", "detalle": _mensaje_error(e)})

    return StreamingResponse(eventos(), media_type="text/event-stream")


def _sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


def _mensaje_error(e: Exception) -> str:
    texto = str(e)
    if "429" in texto or "RESOURCE_EXHAUSTED" in texto or "quota" in texto.lower():
        return "Se alcanzó el límite de uso gratuito del modelo. Intenta de nuevo en unos minutos."
    return "El agente tuvo un problema al responder. Intenta de nuevo."
