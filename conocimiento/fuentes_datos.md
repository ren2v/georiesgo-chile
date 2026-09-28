---
titulo: Fuentes de datos de GeoRiesgo Chile
fuente: README y pipeline de datos del proyecto
url: https://github.com/ren2v/georiesgo-chile
---

# Fuentes de datos de GeoRiesgo Chile

## Geología (SERNAGEOMIN)

Mapa geológico de Chile del Servicio Nacional de Geología y Minería, con unas 16 mil unidades. Cada unidad trae ambiente (sedimentario marino, sedimentario continental, plutónico, volcánico, metamórfico), periodo geológico, litoestratos, descripción litológica y rocas dominantes. El modelo lo usa para el factor suelo.

## Fallas activas (GEM)

Base de datos de fallas activas globales de la Global Earthquake Model Foundation (GEM), recortada a Chile (237 trazas). Incluye nombre de la falla cuando está catalogada, tipo de movimiento (inversa, normal, de rumbo) y referencias. Muchas trazas no tienen nombre y se muestran como "Sin nombre catalogado".

## Sismos (CSN)

Catálogo del Centro Sismológico Nacional de la Universidad de Chile, con sismos de magnitud 4 o más desde 2012: fecha (UTC), latitud, longitud, profundidad y magnitud. Los grandes terremotos anteriores a 2012 se complementan con una tabla curada dentro del modelo.

## Línea de costa

Línea de costa mundial recortada al territorio chileno continental. Se usa para medir la distancia a la costa, que el modelo toma como aproximación de la distancia a la fosa de subducción.

## Cartas de Inundación por Tsunami (SHOA)

Polígonos de inundación de 71 Cartas de Inundación por Tsunami (CITSU) del Servicio Hidrográfico y Oceanográfico de la Armada, unidos por localidad y simplificados (unos 50 m de tolerancia). Se usan solo para informar si un punto costero cae dentro del área de inundación modelada.

## Elevación (Open-Elevation)

Para puntos a menos de 10 km de la costa se consulta la elevación aproximada en la API pública Open-Elevation. Si la consulta falla, la respuesta sigue funcionando sin ese dato.

## Cómo se almacenan

Los datos pueden resolverse en memoria con GeoPandas o en PostgreSQL con PostGIS, con índices espaciales. Ambos backends devuelven los mismos resultados, verificado con tests de paridad.
