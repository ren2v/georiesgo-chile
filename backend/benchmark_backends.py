"""Compara latencia de las consultas espaciales: memoria vs. PostGIS.

    python backend/benchmark_backends.py

Mide cada función sobre una grilla de puntos a lo largo de Chile y reporta
mediana y p95 en milisegundos, más el tiempo de arranque del backend en
memoria (carga de GeoJSON), que PostGIS no paga.
"""
import statistics
import time

import numpy as np

inicio = time.perf_counter()
import geo  # noqa: E402
arranque_memoria = time.perf_counter() - inicio

import geo_postgis  # noqa: E402

PUNTOS = [(lat, lng) for lat in np.linspace(-18.5, -53, 12) for lng in (-72.5, -71.0, -69.5)]

FUNCIONES = {
    "geologia (ST_Contains)": "consultar_geologia",
    "fallas 50 km (ST_DWithin)": "consultar_fallas_cercanas",
    "distancia costa (KNN <->)": "consultar_distancia_costa",
    "sismos 100 km (ST_DWithin)": "consultar_sismos_cercanos",
    "zona CITSU (ST_Contains)": "consultar_zona_inundacion_oficial",
}


def medir(funcion) -> list:
    tiempos = []
    for lat, lng in PUNTOS:
        t0 = time.perf_counter()
        funcion(lat, lng)
        tiempos.append((time.perf_counter() - t0) * 1000)
    return tiempos


def resumen(tiempos: list) -> str:
    p95 = statistics.quantiles(tiempos, n=20)[-1]
    return f"{statistics.median(tiempos):8.1f} {p95:8.1f}"


if __name__ == "__main__":
    assert geo.BACKEND == "memoria", "corre el benchmark sin GEORIESGO_BACKEND=postgis"
    geo_postgis.consultar_geologia(*PUNTOS[0])  # calentar el pool de conexiones

    print(f"Arranque backend en memoria (carga de GeoJSON): {arranque_memoria:.1f} s")
    print(f"{len(PUNTOS)} puntos por consulta. Tiempos en ms (mediana / p95)\n")
    print(f"{'consulta':30} {'memoria':>17} {'postgis':>17}")
    for etiqueta, nombre in FUNCIONES.items():
        m = medir(getattr(geo, nombre))
        p = medir(getattr(geo_postgis, nombre))
        print(f"{etiqueta:30} {resumen(m)} {resumen(p)}")
