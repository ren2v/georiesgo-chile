from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import geo

app = FastAPI(title="GeoRiesgo Chile API")

# Permite que el frontend (en otro puerto/dominio) consuma la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción, restringir al dominio real del frontend
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bounding box aproximado de Chile continental, para validar coordenadas
CHILE_BOUNDS = {"lat_min": -56, "lat_max": -17, "lng_min": -76, "lng_max": -66}

def validar_coordenadas(lat: float, lng: float):
    if not (CHILE_BOUNDS["lat_min"] <= lat <= CHILE_BOUNDS["lat_max"]):
        raise HTTPException(status_code=400, detail="Latitud fuera del territorio chileno")
    if not (CHILE_BOUNDS["lng_min"] <= lng <= CHILE_BOUNDS["lng_max"]):
        raise HTTPException(status_code=400, detail="Longitud fuera del territorio chileno")

@app.get("/")
def raiz():
    return {"mensaje": "GeoRiesgo Chile API", "endpoints": ["/geologia", "/fallas", "/sismos", "/consulta", "/riesgo"]}

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
    """Endpoint combinado: geología + fallas + sismos en un solo llamado, sin evaluación de riesgo."""
    validar_coordenadas(lat, lng)
    return {
        "coordenadas": {"lat": lat, "lng": lng},
        "geologia": geo.consultar_geologia(lat, lng),
        "fallas_cercanas": geo.consultar_fallas_cercanas(lat, lng),
        "sismos_cercanos": geo.consultar_sismos_cercanos(lat, lng)[:10],
    }

@app.get("/riesgo")
def riesgo(lat: float, lng: float):
    """Endpoint principal: evaluación de riesgo con puntaje y factores explicados.
    Este es el que usará el LLM como base para redactar la respuesta al usuario."""
    validar_coordenadas(lat, lng)
    return geo.evaluar_riesgo(lat, lng)