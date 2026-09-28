"""Backend PostGIS: las mismas consultas espaciales de geo.py, resueltas en
la base de datos. Mismas firmas y mismo formato de salida (vía formato.py),
así evaluar_riesgo funciona igual con cualquiera de los dos backends.

Diferencias de diseño respecto de la versión en memoria:
- Distancias sobre `geography` (elipsoide WGS84, en metros) en vez de
  reproyectar la capa completa a UTM 19S en cada consulta. Además de ser más
  rápido, es más correcto: Chile cruza los husos UTM 18 y 19.
- ST_DWithin filtra con el índice GIST antes de calcular distancias exactas,
  así que el costo depende de lo que hay cerca del punto, no del tamaño de la capa.
- ST_Contains usa el índice sobre `geom` para descartar por bounding box los
  ~16 mil polígonos de geología antes de la prueba exacta.
"""
from sqlalchemy import text

from db import obtener_engine
from formato import formatear_geologia, formatear_falla, formatear_sismo

# CTE reutilizable: el punto consultado como geometry y como geography.
PUNTO = """
    WITH p AS (
        SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326) AS geom,
               ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS geog
    )
"""


def _consultar(sql: str, **params) -> list:
    # psycopg2 adapta np.float64 con su repr, que en NumPy 2 es el texto
    # "np.float64(-18.5)" y rompe el SQL. Todos los parámetros son numéricos,
    # así que los pasamos a float de Python.
    params = {k: float(v) for k, v in params.items()}
    with obtener_engine().connect() as conn:
        return conn.execute(text(sql), params).mappings().all()


def consultar_geologia(lat: float, lng: float) -> dict:
    filas = _consultar(PUNTO + """
        SELECT g.ambiente, g.periodos, g.litoestratos, g.litologia,
               g.roca1, g.roca2, g.roca3, g.roca4
        FROM geologia g, p
        WHERE ST_Contains(g.geom, p.geom)
        ORDER BY g.id
        LIMIT 1
    """, lat=lat, lng=lng)

    if not filas:
        return {"encontrado": False}
    return formatear_geologia(filas[0])


def consultar_fallas_cercanas(lat: float, lng: float, radio_km: float = 50) -> list:
    filas = _consultar(PUNTO + """
        SELECT f.name, f.slip_type,
               ST_Distance(f.geog, p.geog) / 1000 AS distancia_km
        FROM fallas f, p
        WHERE ST_DWithin(f.geog, p.geog, :radio_m)
        ORDER BY distancia_km
    """, lat=lat, lng=lng, radio_m=radio_km * 1000)

    return [formatear_falla(f) for f in filas]


def consultar_distancia_costa(lat: float, lng: float) -> float:
    """Distancia mínima (km) a la línea de costa — usada como aproximación de
    cercanía a la fosa de subducción. `<->` es el operador de vecino más
    cercano (KNN): recorre el índice GIST en orden de distancia y se detiene
    en el primero, sin medir contra todos los segmentos de costa."""
    filas = _consultar(PUNTO + """
        SELECT ST_Distance(c.geog, p.geog) / 1000 AS distancia_km
        FROM costa c, p
        ORDER BY c.geog <-> p.geog
        LIMIT 1
    """, lat=lat, lng=lng)

    return float(filas[0]["distancia_km"])


def consultar_sismos_cercanos(
    lat: float,
    lng: float,
    radio_km: float = 100,
    min_magnitud: float = 4.0,
    profundidad_min: float = None,
    profundidad_max: float = None,
) -> list:
    condiciones = ["ST_DWithin(s.geog, p.geog, :radio_m)", "s.magnitud >= :min_magnitud"]
    params = {"lat": lat, "lng": lng, "radio_m": radio_km * 1000, "min_magnitud": min_magnitud}

    if profundidad_min is not None:
        condiciones.append("s.profundidad_km >= :profundidad_min")
        params["profundidad_min"] = profundidad_min
    if profundidad_max is not None:
        condiciones.append("s.profundidad_km <= :profundidad_max")
        params["profundidad_max"] = profundidad_max

    # Solo se interpolan las condiciones fijas de arriba; los valores van
    # siempre como parámetros enlazados, nunca dentro del string SQL.
    filas = _consultar(PUNTO + f"""
        SELECT to_char(s.fecha, 'YYYY-MM-DD HH24:MI:SS') AS fecha,
               s.magnitud, s.profundidad_km,
               ST_Distance(s.geog, p.geog) / 1000 AS distancia_km
        FROM sismos s, p
        WHERE {" AND ".join(condiciones)}
        ORDER BY s.fecha DESC
    """, **params)

    return [formatear_sismo(f) for f in filas]


def consultar_zona_inundacion_oficial(lat: float, lng: float):
    """Ver la versión en geo.py: nombre de la Carta CITSU del SHOA que
    contiene el punto, o None."""
    filas = _consultar(PUNTO + """
        SELECT t.nombre
        FROM tsunami_citsu t, p
        WHERE ST_Contains(t.geom, p.geom)
        ORDER BY t.id
        LIMIT 1
    """, lat=lat, lng=lng)

    return filas[0]["nombre"] if filas else None
