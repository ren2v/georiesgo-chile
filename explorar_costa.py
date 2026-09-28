import geopandas as gpd

costa = gpd.read_file("data/costa/coastline_world.geojson")

# Bounding box de Chile continental (igual que usamos para las fallas)
minx, maxx = -76, -66
miny, maxy = -56, -17

costa_chile = costa.cx[minx:maxx, miny:maxy]

print(f"Segmentos de costa en el bbox de Chile: {len(costa_chile)}")

costa_chile.to_file("data/costa/costa_chile.geojson", driver="GeoJSON")
print("Guardado en data/costa/costa_chile.geojson")