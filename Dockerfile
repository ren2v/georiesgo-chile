# Imagen de la API de GeoRiesgo Chile (FastAPI + agente).
#
# En contenedor la API usa el backend PostGIS: los datos espaciales viven en
# la base, así que los ~90 MB de GeoJSON de data/ no van en la imagen. Para
# cargarlos se monta data/ y se corre el ETL (ver docker-compose.yml).

# --- Etapa 1: dependencias y modelo de embeddings ---------------------------
FROM python:3.14-slim AS dependencias

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

# El modelo de embeddings (~220 MB) se descarga al construir la imagen, no al
# arrancar: así el contenedor no depende de Hugging Face en producción y el
# primer request no espera la descarga.
ENV FASTEMBED_CACHE_PATH=/opt/modelos
RUN python -c "from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/opt/modelos')" \
    # Hugging Face crea parte del caché con permisos solo para root; la API
    # corre sin privilegios y necesita leerlo.
    && chmod -R a+rX /opt/modelos

# --- Etapa 2: imagen final ---------------------------------------------------
FROM python:3.14-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/opt/modelos \
    HF_HUB_OFFLINE=1 \
    GEORIESGO_BACKEND=postgis

COPY --from=dependencias /opt/venv /opt/venv
COPY --from=dependencias /opt/modelos /opt/modelos

WORKDIR /app
COPY backend/ backend/
COPY conocimiento/ conocimiento/

# Sin privilegios de root dentro del contenedor.
RUN useradd --create-home --uid 10001 georiesgo
USER georiesgo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=4)"

# Un solo worker: se escala con réplicas del contenedor, no con procesos.
CMD ["uvicorn", "main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
