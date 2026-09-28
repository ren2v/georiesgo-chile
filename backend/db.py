import os
from functools import lru_cache
from sqlalchemy import create_engine

# Credenciales de desarrollo que calzan con docker-compose.yml. En producción
# se sobreescriben con la variable de entorno DATABASE_URL.
DATABASE_URL_POR_DEFECTO = "postgresql+psycopg2://georiesgo:georiesgo@localhost:5433/georiesgo"


@lru_cache(maxsize=1)
def obtener_engine():
    """Engine único por proceso (SQLAlchemy mantiene su propio pool de
    conexiones), creado recién cuando se necesita — importar este módulo no
    abre ninguna conexión, así el backend en memoria funciona sin base de datos."""
    url = os.getenv("DATABASE_URL", DATABASE_URL_POR_DEFECTO)
    return create_engine(url, pool_pre_ping=True)
