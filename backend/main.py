from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import geo

app = FastAPI(title="GeoRiesgo Chile API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHILE_BOUNDS = {"lat_min": -56, "lat_max": -17, "lng_min": -76, "lng_max": -66}


def validar_coordenadas(lat: float, lng: float):
    if not (CHILE_BOUNDS["lat_min"] <= lat <= CHILE_BOUNDS["lat_max"]):
        raise HTTPException(status_code=400, detail="Latitud fuera del territorio chileno")
    if not (CHILE_BOUNDS["lng_min"] <= lng <= CHILE_BOUNDS["lng_max"]):
        raise HTTPException(status_code=400, detail="Longitud fuera del territorio chileno")


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
