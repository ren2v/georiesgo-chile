# PostGIS + pgvector en una sola base: los datos espaciales del modelo y los
# embeddings del RAG del agente viven juntos, sin un servicio vectorial aparte.
# La imagen base (postgres:16 sobre Debian) ya trae configurado el repositorio
# apt de PGDG, que publica pgvector empaquetado para cada versión de Postgres.
FROM postgis/postgis:16-3.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
