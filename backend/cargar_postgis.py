"""ETL: carga las capas de data/ en PostGIS.

    docker compose up -d
    python backend/cargar_postgis.py

Es idempotente (reemplaza cada tabla completa), así que se puede volver a
correr cada vez que se actualice un GeoJSON o el catálogo de sismos.

Pasos por capa: leer con GeoPandas -> normalizar CRS a EPSG:4326 2D ->
reparar geometrías inválidas -> escribir con to_postgis -> crear índices
espaciales. Las capas que se consultan por distancia llevan además una
columna `geog` (tipo geography), para medir en metros sobre el elipsoide
en vez de reproyectar a UTM en cada consulta.
"""
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon
from sqlalchemy import text

from db import obtener_engine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SRID = 4326


def normalizar_crs(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Lleva la capa a EPSG:4326 en 2D. Las fallas, por ejemplo, vienen en
    EPSG:4979 (lat/lon + altura elipsoidal): PostGIS rechaza mezclar
    geometrías 3D con puntos 2D en funciones como ST_Distance."""
    gdf = gdf.copy()
    gdf.geometry = shapely.force_2d(gdf.geometry.values)
    if gdf.crs is None or gdf.crs.to_epsg() != SRID:
        gdf = gdf.set_crs(gdf.crs or SRID, allow_override=True).to_crs(SRID)
    return gdf


def a_multipoligono(geom) -> MultiPolygon:
    """make_valid puede devolver Polygon, MultiPolygon o una
    GeometryCollection con restos lineales; nos quedamos solo con la parte
    poligonal (la que importa para ST_Contains) y siempre como MultiPolygon,
    para que la columna tenga un tipo único."""
    poligonos = []
    for parte in shapely.get_parts(geom):
        if parte.geom_type == "Polygon":
            poligonos.append(parte)
        elif parte.geom_type == "MultiPolygon":
            poligonos.extend(parte.geoms)
    return MultiPolygon(poligonos)


def reparar_poligonos(gdf: gpd.GeoDataFrame, nombre: str) -> gpd.GeoDataFrame:
    """Repara polígonos inválidos (auto-intersecciones, anillos mal
    orientados). Con geometrías inválidas, contains() puede dar resultados
    incorrectos sin lanzar ningún error."""
    invalidas = int((~gdf.is_valid).sum())
    if invalidas:
        print(f"  {nombre}: reparando {invalidas} geometrías inválidas")
    gdf = gdf.copy()
    gdf.geometry = [a_multipoligono(g) for g in shapely.make_valid(gdf.geometry.values)]
    return gdf[~gdf.geometry.is_empty]


def leer_sismos() -> gpd.GeoDataFrame:
    df = pd.read_csv(DATA_DIR / "sismos" / "sismos_csn.csv").rename(columns={
        "Fecha (UTC)": "fecha",
        "Latitud [º]": "lat",
        "Longitud [º]": "lng",
        "Profundidad [km]": "profundidad_km",
        "Magnitud [*]": "magnitud",
    })
    df["fecha"] = pd.to_datetime(df["fecha"])
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lng"], df["lat"]), crs=SRID)


def escribir(gdf: gpd.GeoDataFrame, tabla: str, conn, *, con_geography: bool) -> None:
    # Índice explícito `id` = orden original del archivo, para que
    # "la primera coincidencia" sea la misma que en el backend en memoria.
    gdf = gdf.rename_geometry("geom").reset_index(drop=True)
    gdf.to_postgis(tabla, conn, if_exists="replace", index=True, index_label="id")

    conn.execute(text(f"ALTER TABLE {tabla} ADD PRIMARY KEY (id)"))
    conn.execute(text(f"CREATE INDEX {tabla}_geom_gist ON {tabla} USING GIST (geom)"))
    if con_geography:
        conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN geog geography"))
        conn.execute(text(f"UPDATE {tabla} SET geog = geom::geography"))
        conn.execute(text(f"CREATE INDEX {tabla}_geog_gist ON {tabla} USING GIST (geog)"))
    conn.execute(text(f"ANALYZE {tabla}"))


def main():
    capas = [
        # (tabla, lector, es_poligonal, necesita_geography)
        ("geologia", lambda: gpd.read_file(DATA_DIR / "geologia" / "geologia.geojson"), True, False),
        ("fallas", lambda: gpd.read_file(DATA_DIR / "fallas" / "fallas_chile.geojson"), False, True),
        ("costa", lambda: gpd.read_file(DATA_DIR / "costa" / "costa_chile.geojson"), False, True),
        ("tsunami_citsu", lambda: gpd.read_file(DATA_DIR / "tsunami" / "citsu_chile.geojson"), True, False),
        ("sismos", leer_sismos, False, True),
    ]

    engine = obtener_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))

    for tabla, leer, es_poligonal, con_geography in capas:
        inicio = time.perf_counter()
        gdf = normalizar_crs(leer())
        if es_poligonal:
            gdf = reparar_poligonos(gdf, tabla)
        # Una transacción por capa: si una falla, las ya cargadas quedan intactas
        with engine.begin() as conn:
            escribir(gdf, tabla, conn, con_geography=con_geography)
        print(f"  {tabla}: {len(gdf)} filas en {time.perf_counter() - inicio:.1f}s")

    print("Carga completa.")


if __name__ == "__main__":
    main()
