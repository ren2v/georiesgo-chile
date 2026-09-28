# GeoRiesgo Chile

API (FastAPI) que estima el riesgo sísmico de un punto en Chile combinando
geología, fallas activas, sismicidad (CSN), exposición a la fosa de
subducción y cartas oficiales de inundación por tsunami (SHOA/CITSU).

## Backends de datos espaciales

La lógica del modelo (`backend/geo.py`) es independiente de dónde viven los
datos. Se elige con la variable de entorno `GEORIESGO_BACKEND`:

| Backend | Cómo resuelve las consultas | Cuándo usarlo |
|---|---|---|
| `memoria` (defecto) | GeoPandas/Shapely sobre GeoJSON cargados al iniciar | Desarrollo rápido, sin dependencias |
| `postgis` | SQL espacial en PostgreSQL + PostGIS con índices GIST | Producción: arranque instantáneo, sin ~80 MB por proceso |

### Levantar PostGIS

```bash
docker compose up -d
pip install -r requirements.txt
python backend/cargar_postgis.py        # ETL: GeoJSON/CSV -> PostGIS
GEORIESGO_BACKEND=postgis uvicorn main:app --app-dir backend
```

`DATABASE_URL` sobreescribe la conexión por defecto
(`postgresql+psycopg2://georiesgo:georiesgo@localhost:5434/georiesgo`).

### Qué hace el ETL (`backend/cargar_postgis.py`)

- Normaliza todas las capas a EPSG:4326 2D (las fallas vienen en EPSG:4979, 3D).
- Repara con `make_valid` los 68 polígonos de geología inválidos y unifica el tipo a `MultiPolygon`.
- Construye puntos desde el CSV de sismos del CSN, con la fecha como `timestamp`.
- Crea índices GIST sobre `geom` y, en las capas consultadas por distancia, una columna `geography` indexada.

### Consultas espaciales (`backend/geo_postgis.py`)

| Consulta | PostGIS |
|---|---|
| Unidad geológica / zona CITSU que contiene el punto | `ST_Contains` + índice GIST |
| Fallas y sismos en un radio | `ST_DWithin` sobre `geography` (metros, elipsoide WGS84) |
| Distancia a la costa | vecino más cercano con el operador KNN `<->` |

Las distancias se miden sobre el elipsoide en vez de reproyectar a UTM 19S:
Chile cruza los husos 18 y 19, así que un solo huso distorsiona en los extremos.

## Agente (en construcción)

Un agente conversacional que responde preguntas como "¿qué tan riesgosa es
Av. Providencia 1234?" combinando las consultas espaciales del modelo con
búsqueda en documentos (RAG).

### Base de conocimiento y RAG (`backend/agente/rag.py`)

- `conocimiento/`: notas en markdown con la metodología del modelo, las
  fuentes de datos y resúmenes propios de documentos oficiales (SENAPRED,
  SHOA, NCh433), cada una con su fuente en el encabezado.
- Se trocean por sección y cada fragmento lleva el título del documento,
  para que una sección como "Antes: preparación" no pierda de qué habla.
- Embeddings locales con `fastembed` (modelo multilingüe de 384 dimensiones,
  ONNX, sin GPU ni API externa), guardados en **pgvector dentro de la misma
  base que PostGIS** (`docker/db/Dockerfile` agrega la extensión).

```bash
python backend/agente/ingesta.py   # indexa conocimiento/ en pgvector
```

### Herramientas (`backend/agente/herramientas.py`)

| Herramienta | Qué hace |
|---|---|
| `geocodificar_direccion` | Dirección → coordenadas (Nominatim/OpenStreetMap, restringido a Chile) |
| `evaluar_riesgo_sismico` | Nivel y puntaje de riesgo con el detalle por factor |
| `buscar_sismos_cercanos` | Sismos del catálogo CSN en un radio |
| `buscar_fallas_cercanas` | Fallas activas (GEM) en un radio |
| `buscar_en_documentos` | Búsqueda semántica en la base de conocimiento |

Los errores (punto fuera de Chile, dirección no encontrada, servicio caído)
vuelven como datos y no como excepciones, para que el modelo pueda corregirse.

### Tests

```bash
cd backend && pytest
```

`test_postgis.py` verifica la **paridad** entre ambos backends en puntos de
todo el país (se salta automáticamente si PostGIS no está disponible), y
`benchmark_backends.py` compara sus latencias. `test_agente.py` cubre las
herramientas y el RAG, incluida una regresión de calidad de búsqueda: para
preguntas conocidas, el documento correcto debe quedar entre los 3 primeros.
