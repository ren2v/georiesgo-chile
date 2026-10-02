# GeoRiesgo Chile

Estimación del riesgo sísmico relativo de cualquier punto de Chile, con un
**asistente de IA** que responde preguntas como *"¿qué tan riesgosa es Av.
Providencia 1234?"* consultando datos geoespaciales reales y documentos
oficiales.

**Demo:** [georiesgo-chile.vercel.app](https://georiesgo-chile.vercel.app)
(mapa interactivo; el asistente funciona en local y su despliegue en Azure
está en curso).

## Qué hace

- Combina **geología** (SERNAGEOMIN), **fallas activas** (GEM), **sismicidad**
  (CSN), **exposición a la fosa de subducción** y **cartas oficiales de
  inundación por tsunami** (SHOA) en un puntaje de riesgo explicado factor
  por factor.
- Un **agente LLM** geocodifica direcciones, llama a las consultas
  espaciales como herramientas y cita documentos oficiales (SENAPRED, SHOA,
  NCh433) para explicar los resultados.

## Destacados técnicos

| Área | Qué hay |
|---|---|
| **Agente LLM** | LangChain/LangGraph conectado vía API a Google Gemini: *tool calling*, streaming (SSE), memoria de conversación, reintentos y cadena de modelos de respaldo ante saturación y cuotas |
| **RAG** | Embeddings locales multilingües en **pgvector**, en la misma base que PostGIS |
| **Evals** | 16 casos con verificaciones deterministas, incluida la consistencia entre la respuesta y los datos de las herramientas (detecta resultados inventados) |
| **Datos espaciales** | PostGIS con índices GIST y distancias sobre el elipsoide; backend en memoria (GeoPandas) alternativo, con tests de paridad entre ambos |
| **Contenedores** | Imagen multi-etapa sin root; Docker Compose con base, ETL, API y frontend |
| **Infraestructura** | Terraform para Azure (Container Apps, PostgreSQL Flexible Server, Static Web Apps, Log Analytics), diseñado para costo cero en Azure for Students |
| **Calidad** | 165 tests (pytest), incluido el loop del agente con un modelo falso para no gastar cuota |

## Arquitectura

```
Frontend (Leaflet) ──> API FastAPI ──┬──> Modelo de riesgo (geo.py) ──> PostGIS
                                     └──> Agente (LangGraph) ──┬──> LLM (Gemini, vía API)
                                                               ├──> Herramientas: geocodificación, riesgo, sismos, fallas
                                                               └──> RAG ──> pgvector
```

| Capa | Tecnologías |
|---|---|
| Backend | Python 3.14, FastAPI, GeoPandas, Shapely, SQLAlchemy |
| IA | LangChain, LangGraph, Google Gemini API, fastembed, pgvector |
| Datos | PostgreSQL 16, PostGIS 3.4 |
| Frontend | HTML/JS, Leaflet |
| Infra | Docker, Docker Compose, Terraform, Azure |

## Cómo correrlo

```bash
docker compose up -d db
docker compose --profile carga run --rm carga   # ETL + indexación del RAG (una vez)
docker compose up -d                            # API en :8000, frontend en :8080
```

Para el asistente, crea un archivo `.env` en la raíz (está en `.gitignore`)
con una key gratuita de [Google AI Studio](https://aistudio.google.com):

```bash
GOOGLE_API_KEY=...
```

Sin la key, todo funciona salvo `/agente`, que responde 503.

---

## Agente

Construido con LangChain (`create_agent`, sobre LangGraph) e independiente
del proveedor del modelo: se cambia con una variable de entorno.

```bash
# GEORIESGO_MODELO=google_genai:gemini-3.5-flash-lite   # por defecto
# GEORIESGO_MODELOS_RESPALDO=google_genai:gemini-3.7-flash,...   # cadena de respaldo
# GEORIESGO_MODELO=anthropic:claude-opus-5          # otro proveedor: requiere langchain-anthropic y ANTHROPIC_API_KEY
```

```bash
python backend/agente/consola.py    # chat en la terminal
uvicorn main:app --app-dir backend  # API: POST /agente responde en streaming (SSE)
```

El frontend tiene una pestaña **Asistente**: cuando el agente evalúa un
lugar, el mapa vuela a ese punto.

### Cómo funciona (`backend/agente/agente.py`)

- Prompt de sistema que obliga a sacar cualquier dato de las herramientas
  (nunca cifras de memoria), a aclarar que el puntaje es relativo y a
  recomendar profesionales para decisiones de construcción.
- Topes por pregunta con middleware de LangChain: máximo 6 llamadas al
  modelo y 10 a herramientas, para que un loop no consuma la cuota.
- Memoria de conversación por `conversacion_id` (checkpointer de LangGraph).
- Streaming de eventos: qué herramienta usa y el texto a medida que se genera.

### Resiliencia ante la API del modelo

En el tier gratuito de Gemini la cuota diaria es **por modelo**: 500
peticiones/día en los Flash Lite, pero solo 20 en los Flash (3.5 a 3.8), que
no alcanzan ni para una corrida de evals. Los modelos recién lanzados además
suelen responder 503 por saturación, y esos errores también descuentan
cuota. Por eso el principal es `gemini-3.5-flash-lite` y el agente:

- reintenta solo errores transitorios (503, límite por minuto) con espera
  exponencial, y pasa directo al siguiente modelo si se agotó la cuota diaria;
- recorre una cadena de modelos de respaldo, cada uno con su propia cuota;
- desactiva los reintentos internos del cliente de Gemini (6 por defecto y
  sin timeout), que multiplicados por la cadena agotaban la cuota en segundos.

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

### Evals (`evals/casos.yaml`, `backend/agente/evaluar.py`)

16 casos con verificaciones deterministas, sin LLM como juez: herramientas
esperadas y en qué orden, que el nivel de riesgo de la respuesta coincida
con el que devolvió la herramienta (detecta resultados inventados),
contenido obligatorio (p. ej. "evacuar" ante un sismo en la playa) y
rechazo de preguntas fuera de tema. Los errores de servicio del modelo se
informan aparte, para no mezclar calidad con disponibilidad.

```bash
python backend/agente/evaluar.py               # guarda el detalle en evals/resultados/
python backend/agente/evaluar.py --reintentar  # solo los casos con error de servicio
```

Última corrida versionada: 16/16 casos, 40/40 verificaciones con
`gemini-3.5-flash-lite`. Las respuestas del modelo no son deterministas, así
que una sola corrida no equivale a una tasa de acierto.

## Datos espaciales

La lógica del modelo (`backend/geo.py`) es independiente de dónde viven los
datos. Se elige con la variable de entorno `GEORIESGO_BACKEND`:

| Backend | Cómo resuelve las consultas | Cuándo usarlo |
|---|---|---|
| `memoria` (defecto) | GeoPandas/Shapely sobre GeoJSON cargados al iniciar | Desarrollo rápido, sin dependencias |
| `postgis` | SQL espacial en PostgreSQL + PostGIS con índices GIST | Producción: arranque instantáneo, sin ~80 MB por proceso |

### Desarrollo sin Docker para la API

```bash
docker compose up -d db
pip install -r requirements-dev.txt
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

## Contenedores

La imagen de la API (`Dockerfile`) es multi-etapa, corre sin root, trae el
modelo de embeddings horneado (no depende de Hugging Face al arrancar) y usa
el backend PostGIS, así que los GeoJSON de `data/` no van dentro: se montan
solo en el job de carga.

## Infraestructura en Azure (`infra/`)

Terraform para Container Apps (API, escala a cero), PostgreSQL Flexible
Server con PostGIS y pgvector, Static Web Apps (frontend) y Log Analytics,
con estado remoto en Azure Storage. Está pensado para costar US$0 en una
suscripción Azure for Students. Despliegue en curso.

## Tests

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
