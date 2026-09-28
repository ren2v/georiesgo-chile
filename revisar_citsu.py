import geopandas as gpd

gdf = gpd.read_file("data/tsunami/citsu_chile.geojson")
print(f"Total de cartas cargadas: {len(gdf)}\n")

print("Nombres que contienen 'alpara' (Valparaíso):")
for n in gdf["nombre"]:
    if "alpara" in n.lower():
        print(f"  {n}")

print("\nPrimeros 15 nombres (para ver el patrón de nombres real):")
for n in gdf["nombre"].head(15):
    print(f"  {n}")
