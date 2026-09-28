"""
Une las Cartas de Inundación por Tsunami (CITSU) del SHOA — descargadas
manualmente como KMZ individuales en data/tsunami/kmz_raw/ — en una sola
capa geoespacial (data/tsunami/citsu_chile.geojson).

Cada KMZ puede tener varias capas internas (distintos niveles de inundación,
colores, etc.). Para este proyecto no necesitamos esa granularidad — solo
"¿este punto cae dentro de alguna zona de inundación modelada por el SHOA
para esta localidad?" — así que unimos todos los polígonos de cada archivo
en una sola geometría por localidad.

Usa pyogrio (ya viene con geopandas) en vez de fiona, porque fiona requiere
compilar contra GDAL desde código fuente y eso falla en Windows sin
herramientas de compilación instaladas.
"""

import geopandas as gpd
import pyogrio
from pathlib import Path
from shapely.ops import unary_union

CARPETA_KMZ = Path("data/tsunami/kmz_raw")
SALIDA = Path("data/tsunami/citsu_chile.geojson")

# Las cartas CITSU vienen de restitución fotogramétrica de alta resolución —
# mucho más detalle del que necesitamos para "¿está el punto dentro o no?".
# Simplificamos con Douglas-Peucker (tolerancia en grados; ~0.0005° ≈ 50m)
# para que el archivo sea manejable en memoria y en GitHub, sin cambiar el
# resultado práctico de la consulta punto-en-polígono para nuestro uso.
TOLERANCIA_SIMPLIFICACION = 0.0005


def procesar_kmz(ruta: Path):
    """Lee todas las capas de un KMZ y devuelve una sola geometría unida
    (todos los polígonos de inundación de esa carta, sin separar niveles)."""
    geometrias = []

    try:
        capas_info = pyogrio.list_layers(str(ruta))
    except Exception as e:
        print(f"  No se pudo leer la lista de capas: {e}")
        return None

    nombres_capas = [c[0] for c in capas_info]

    for capa in nombres_capas:
        try:
            gdf = gpd.read_file(ruta, layer=capa)
        except Exception as e:
            print(f"  Capa '{capa}' no se pudo leer: {e}")
            continue

        # Nos interesan solo geometrías de área (polígonos) — el KML de
        # Google Earth a veces incluye puntos/líneas decorativas (etiquetas,
        # bordes) que no representan zona de inundación.
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        if not gdf.empty:
            geometrias.extend(gdf.geometry.tolist())

    if not geometrias:
        return None

    union = unary_union(geometrias)
    return union.simplify(TOLERANCIA_SIMPLIFICACION, preserve_topology=True)


def main():
    archivos = sorted(CARPETA_KMZ.glob("*.kmz"))
    print(f"Encontrados {len(archivos)} archivos KMZ en {CARPETA_KMZ}")

    filas = []
    fallidos = []

    for i, ruta in enumerate(archivos, 1):
        nombre = ruta.stem  # nombre del archivo sin extensión, ej "CITSU_Valparaiso_..."
        print(f"[{i}/{len(archivos)}] {nombre}")
        geometria = procesar_kmz(ruta)
        if geometria is None or geometria.is_empty:
            fallidos.append(nombre)
            continue
        filas.append({"nombre": nombre, "geometry": geometria})

    if not filas:
        print("\nNo se pudo procesar ningún archivo. Revisa el mensaje de error de arriba.")
        return

    resultado = gpd.GeoDataFrame(filas, crs="EPSG:4326")
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    resultado.to_file(SALIDA, driver="GeoJSON")

    print(f"\nListo: {len(resultado)} cartas unidas en {SALIDA}")
    tamano_mb = SALIDA.stat().st_size / (1024 * 1024)
    print(f"Tamaño del archivo: {tamano_mb:.1f} MB")
    if fallidos:
        print(f"\n{len(fallidos)} archivo(s) no se pudieron procesar (revisar a mano):")
        for f in fallidos:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
