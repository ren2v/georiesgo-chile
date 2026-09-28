import os
from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

# Credenciales de desarrollo que calzan con docker-compose.yml. En producción
# se sobreescriben con la variable de entorno DATABASE_URL.
DATABASE_URL_POR_DEFECTO = "postgresql+psycopg2://georiesgo:georiesgo@localhost:5434/georiesgo"


def obtener_url(driver: str = "psycopg2") -> str:
    """URL de conexión con el driver pedido. Las consultas espaciales usan
    psycopg2 (vía SQLAlchemy); langchain-postgres exige psycopg 3, así que el
    RAG pide la misma URL con driver "psycopg"."""
    url = make_url(os.getenv("DATABASE_URL", DATABASE_URL_POR_DEFECTO))
    return url.set(drivername=f"postgresql+{driver}").render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def obtener_engine():
    """Engine único por proceso (SQLAlchemy mantiene su propio pool de
    conexiones), creado recién cuando se necesita — importar este módulo no
    abre ninguna conexión, así el backend en memoria funciona sin base de datos."""
    return create_engine(obtener_url(), pool_pre_ping=True)
