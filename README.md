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

## Agente

Un agente conversacional que responde preguntas como "¿qué tan riesgosa es
Av. Providencia 1234?" combinando las consultas espaciales del modelo con
búsqueda en documentos (RAG). Está construido con LangChain (`create_agent`,
sobre LangGraph) y es independiente del proveedor del modelo.

### Configuración

Crea un archivo `.env` en la raíz (está en `.gitignore`):

```bash
GOOGLE_API_KEY=...                                  # key gratuita de aistudio.google.com
# GEORIESGO_MODELO=google_genai:gemini-3.6-flash    # por defecto
# GEORIESGO_MODELOS_RESPALDO=google_genai:gemini-3.7-flash,...   # cadena de respaldo
# GEORIESGO_MODELO=anthropic:claude-opus-5          # otro proveedor: requiere langchain-anthropic y ANTHROPIC_API_KEY
```

En el tier gratuito de Gemini la cuota diaria es **por modelo** y baja
(p. ej. 20 peticiones/día en `gemini-3.8-flash`), y los modelos recién
lanzados suelen responder 503 por saturación. Por eso el agente:

- reintenta solo errores transitorios (503, límite por minuto) con espera
  exponencial, y pasa directo al siguiente modelo si se agotó la cuota diaria;
- recorre una cadena de modelos de respaldo, cada uno con su propia cuota;
- desactiva los reintentos internos del cliente de Gemini (6 por defecto y
  sin timeout), que multiplicados por la cadena agotaban la cuota en segundos.

```bash
python backend/agente/consola.py    # chat en la terminal
uvicorn main:app --app-dir backend  # API: POST /agente responde en streaming (SSE)
```

El frontend tiene una pestaña **Asistente**: cuando el agente evalúa un
lugar, el mapa vuela a ese punto. Con el frontend servido desde localhost
apunta a la API local.

### Cómo funciona (`backend/agente/agente.py`)

- Prompt de sistema que obliga a sacar cualquier dato de las herramientas
  (nunca cifras de memoria), a aclarar que el puntaje es relativo y a
  recomendar profesionales para decisiones de construcción.
- Topes por pregunta con middleware de LangChain: máximo 6 llamadas al
  modelo y 10 a herramientas, para que un loop no consuma la cuota.
- Memoria de conversación por `conversacion_id` (checkpointer de LangGraph).
- Streaming de eventos: qué herramienta usa y el texto a medida que se genera.

### Evals (`evals/casos.yaml`, `backend/agente/evaluar.py`)

16 casos con verificaciones deterministas, sin LLM como juez: herramientas
esperadas y en qué orden, que el nivel de riesgo de la respuesta coincida
con el que devolvió la herramienta (detecta resultados inventados),
contenido obligatorio (p. ej. "evacuar" ante un sismo en la playa) y
rechazo de preguntas fuera de tema.

```bash
python backend/agente/evaluar.py   # guarda el detalle en evals/resultados/
```

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
`test_agente_loop.py` prueba el loop del agente y el endpoint con un modelo
falso (sin gastar cuota) y `test_evals.py`, las verificaciones de las evals.
