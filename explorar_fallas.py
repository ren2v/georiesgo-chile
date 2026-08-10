import geopandas as gpd

fallas_chile = gpd.read_file("data/fallas/fallas_chile.geojson")

conocidas = fallas_chile[fallas_chile['name'].notna()]
print(f"Fallas con nombre: {len(conocidas)}")
print(conocidas[['name', 'slip_type']].to_string())