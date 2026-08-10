import geopandas as gpd

geologia = gpd.read_file("data/geologia/geologia.geojson")

# Ver contenido completo de una fila, sin cortar texto
import pandas as pd
pd.set_option('display.max_colwidth', None)

fila = geologia.iloc[2]
print("--- Ejemplo de una fila completa ---")
for col in ['cd_geol', 'resumen', 'ambiente', 'litoestratos', 'periodos', 'litologia', 'roca1', 'roca2', 'roca3', 'roca4']:
    print(f"{col}: {fila[col]}")

print("\n--- Valores únicos de 'ambiente' (primeros 15) ---")
print(geologia['ambiente'].dropna().unique()[:15])

print("\n--- Valores únicos de 'roca1' (primeros 15) ---")
print(geologia['roca1'].dropna().unique()[:15])