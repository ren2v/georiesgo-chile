"""Paridad entre el backend en memoria (geo.py) y el backend PostGIS
(geo_postgis.py): para un mismo punto, ambos deben devolver lo mismo.

Requiere la base levantada y cargada (docker compose up -d && python
backend/cargar_postgis.py); si no está disponible, estos tests se saltan.
"""
import pytest
from sqlalchemy import text

import geo
from db import obtener_engine


def _postgis_disponible() -> bool:
    try:
        with obtener_engine().connect() as conn:
            conn.execute(text("SELECT 1 FROM geologia LIMIT 1"))
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(geo.BACKEND != "memoria", reason="la paridad compara contra el backend en memoria"),
    pytest.mark.skipif(not _postgis_disponible(), reason="PostGIS no disponible o sin datos cargados"),
]

import geo_postgis  # noqa: E402  (después de los skip, para no fallar al importar)

PUNTOS = {
    "Santiago centro": (-33.45, -70.65),
    "Antofagasta centro": (-23.65, -70.40),
    "San Pedro de Atacama": (-22.91, -68.20),
    "Piedemonte oriente": (-33.45, -70.54),
    "Valdivia centro": (-39.8142, -73.2459),
    "Iquique costa": (-20.2141, -70.1524),
    "Valparaíso plan": (-33.0400, -71.6200),
    "Concepción": (-36.8270, -73.0500),
    "Punta Arenas": (-53.1638, -70.9171),
    "Océano frente a Chiloé": (-42.5, -76.0),
}
IDS = list(PUNTOS)


def distancia_equivalente(km_memoria: float, km_postgis: float) -> bool:
    """El backend en memoria mide en UTM 19S y PostGIS sobre el elipsoide:
    difieren levemente lejos del meridiano central del huso (~0.1% en
    Valdivia, algo más en Punta Arenas). Aceptamos 1% o 0.2 km."""
    return km_postgis == pytest.approx(km_memoria, rel=0.01, abs=0.2)


@pytest.mark.parametrize("nombre", IDS)
def test_geologia_paridad(nombre):
    lat, lng = PUNTOS[nombre]
    assert geo_postgis.consultar_geologia(lat, lng) == geo.consultar_geologia(lat, lng)


@pytest.mark.parametrize("nombre", IDS)
def test_fallas_paridad(nombre):
    lat, lng = PUNTOS[nombre]
    memoria = geo.consultar_fallas_cercanas(lat, lng, radio_km=50)
    postgis = geo_postgis.consultar_fallas_cercanas(lat, lng, radio_km=50)

    # Una falla justo en el borde del radio puede quedar dentro en un
    # backend y fuera en el otro; comparamos las que están claramente dentro.
    claras = lambda fallas: [f for f in fallas if f["distancia_km"] < 49]
    memoria, postgis = claras(memoria), claras(postgis)

    assert len(postgis) == len(memoria)
    for m, p in zip(memoria, postgis):
        assert p["nombre"] == m["nombre"]
        assert p["tipo_movimiento"] == m["tipo_movimiento"]
        assert distancia_equivalente(m["distancia_km"], p["distancia_km"])


@pytest.mark.parametrize("nombre", IDS)
def test_distancia_costa_paridad(nombre):
    lat, lng = PUNTOS[nombre]
    assert distancia_equivalente(geo.consultar_distancia_costa(lat, lng), geo_postgis.consultar_distancia_costa(lat, lng))


@pytest.mark.parametrize("nombre", IDS)
@pytest.mark.parametrize("filtros", [
    {"radio_km": 100, "min_magnitud": 4.0},
    {"radio_km": 30, "min_magnitud": 4.0, "profundidad_max": 30},
    {"radio_km": 200, "min_magnitud": 6.0, "profundidad_min": 30},
])
def test_sismos_paridad(nombre, filtros):
    lat, lng = PUNTOS[nombre]
    memoria = geo.consultar_sismos_cercanos(lat, lng, **filtros)
    postgis = geo_postgis.consultar_sismos_cercanos(lat, lng, **filtros)

    # Haversine (esfera) vs. elipsoide: descartamos los que están en el
    # borde del radio y comparamos como conjuntos (el orden entre sismos con
    # la misma fecha no está definido en ninguno de los dos backends).
    borde = filtros["radio_km"] * 0.99
    clave = lambda s: (s["fecha"], s["magnitud"], s["profundidad_km"])
    assert {clave(s) for s in postgis if s["distancia_km"] < borde} == \
           {clave(s) for s in memoria if s["distancia_km"] < borde}


@pytest.mark.parametrize("nombre", IDS)
def test_zona_inundacion_paridad(nombre):
    lat, lng = PUNTOS[nombre]
    assert geo_postgis.consultar_zona_inundacion_oficial(lat, lng) == geo.consultar_zona_inundacion_oficial(lat, lng)
