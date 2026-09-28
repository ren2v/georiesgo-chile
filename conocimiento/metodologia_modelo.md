---
titulo: Metodología del modelo de riesgo de GeoRiesgo Chile
fuente: backend/geo.py (código del proyecto)
url: https://github.com/ren2v/georiesgo-chile
---

# Metodología del modelo de riesgo de GeoRiesgo Chile

## Qué mide el puntaje

GeoRiesgo Chile entrega un puntaje de 0 a 100 % y un nivel (Bajo, Moderado o Alto) para un punto de Chile. Es una **comparación relativa entre puntos del país**, no una probabilidad de terremoto ni una medida absoluta de peligro: todo Chile es sísmicamente activo y toda construcción debe cumplir la norma NCh433. No reemplaza un estudio de mecánica de suelos ni la opinión de un ingeniero o geólogo.

El puntaje es una suma ponderada de cuatro factores, cada uno normalizado entre 0 y 1:

| Factor | Peso máximo | Qué mide |
|---|---|---|
| Falla | 5 | Cercanía a la falla activa catalogada más cercana |
| Sismicidad cortical | 2 | Sismos superficiales (menos de 30 km de profundidad) cerca del punto |
| Exposición a subducción | 4 | Cercanía a la costa y grandes sismos de interfaz en la zona |
| Suelo | 2 | Tipo de unidad geológica bajo el punto |

Puntaje = 100 × (suma de contribuciones) / 13. Nivel **Alto** desde 60 %, **Moderado** desde 30 %, **Bajo** bajo 30 %.

## Factor falla

Busca fallas activas en 50 km. La más cercana aporta 1,0 si está encima del punto y decae linealmente hasta 0 a los 30 km. Más allá de 30 km una falla no suma al puntaje, aunque se informa.

## Factor sismicidad cortical

Considera sismos de magnitud 4 o más, a menos de 30 km del punto y con profundidad menor a 30 km: son los únicos atribuibles a estructuras superficiales locales. La magnitud máxima se normaliza entre M4 (0) y M7 (1).

Si hay una falla a menos de 15 km pero no hay sismicidad cortical registrada, el modelo lo explica como "silencio sísmico": fallas como la de San Ramón, en el borde oriente de Santiago, tienen su peligrosidad establecida por paleosismología (trincheras que muestran rupturas antiguas), no por sismos instrumentales recientes. Silencio no significa ausencia de riesgo.

## Factor exposición a subducción

Chile está sobre el borde de subducción de la placa de Nazca bajo la Sudamericana. La fosa corre mar adentro, paralela a toda la costa, así que la distancia a la costa se usa como aproximación de la cercanía a la fosa. El factor combina:

- **60 % cercanía a la costa**: 1,0 en la costa, decae linealmente a 0 a los 150 km.
- **20 % magnitud del mayor sismo de interfaz** en 200 km (profundidad de 30 km o más, M6 o más), normalizada entre M6 y M9,5 (Valdivia 1960, el mayor registrado).
- **20 % laguna sísmica**: ver abajo.

Como el catálogo instrumental del CSN usado parte en 2012, el modelo lo complementa con una tabla de grandes terremotos históricos: Valdivia 1960 (M9,5), Maule 2010 (M8,8), Illapel 2015 (M8,3), Iquique 2014 (M8,2), Valparaíso 1985 (M8,0) y Antofagasta 1995 (M8,0).

### Laguna sísmica (ventana de alivio post-ruptura)

Un gran terremoto libera tensión acumulada en su segmento. El modelo usa una ventana de alivio de 30 años: justo después de un gran sismo el componente vale 0 y sube linealmente hasta 1 a los 30 años. El alivio se escala por la magnitud del evento más reciente: un M6 casi no alivia la tensión del segmento, un M9,5 la alivia casi por completo. Pasados los 30 años el modelo no extrapola que "más tiempo sin romper = más peligro", porque eso requeriría conocer el ciclo sísmico real de cada segmento. Si no hay ningún evento documentado en 200 km, el componente queda neutral en 0,5.

## Factor suelo

Usa la unidad geológica de SERNAGEOMIN bajo el punto:

- Depósitos sedimentarios continentales (aluviales): 1,0, por mayor amplificación sísmica y posible licuefacción.
- Roca plutónica o metamórfica: 0, generalmente más estable.
- Otros ambientes o sin dato: 0,5.

Es una aproximación gruesa: la clasificación sísmica de suelos de la norma chilena se basa en mediciones en terreno (velocidad de ondas de corte), no en el mapa geológico.

## Anulación por amenaza extrema

Si un solo factor es extremo (falla o exposición a subducción con valor 0,85 o más), el nivel pasa a **Alto** aunque el puntaje total no llegue a 60 %: una amenaza de esa magnitud no necesita combinarse con otras para justificar máxima cautela.

## Notas informativas que no afectan el puntaje

- **Tsunami**: para puntos a menos de 10 km de la costa, el modelo revisa si el punto cae dentro de una Carta de Inundación por Tsunami (CITSU) del SHOA y lo informa citando la carta. No calcula inundación propia. También agrega la elevación aproximada del punto si está disponible.
- **Zonas de acumulación identificadas por expertos**: tramos de costa que el geógrafo Marcelo Lagos (PUC) señaló como con energía acumulada significativa (Pisagua–límite con Perú, Punta Patache–Tocopilla, costa de Atacama, Los Vilos–Pichilemu, Tirúa al sur). Se muestran como evidencia cualitativa citada, no entran al cálculo.

## Limitaciones conocidas

- La distancia a la costa es solo un proxy de la distancia a la fosa.
- El catálogo instrumental cubre desde 2012; los eventos anteriores dependen de una tabla curada de seis terremotos.
- El suelo se infiere del mapa geológico, no de mediciones geotécnicas.
- Los pesos y umbrales son una calibración propia del proyecto, no oficial.
