"""RAG sobre la base de conocimiento del proyecto (carpeta conocimiento/):
metodología del modelo, fuentes de datos y resúmenes de documentos oficiales
(SENAPRED, SHOA, NCh433). Los embeddings se calculan localmente y se guardan
en pgvector, en la misma base PostgreSQL que los datos de PostGIS."""
import asyncio
import os
import re
import selectors
import sys
from functools import lru_cache
from pathlib import Path
from threading import Thread

import yaml
from fastembed import TextEmbedding
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import Column, PGEngine, PGVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter
from sqlalchemy.ext.asyncio import create_async_engine

from db import obtener_url

CARPETA_CONOCIMIENTO = Path(__file__).resolve().parents[2] / "conocimiento"
TABLA = "conocimiento"

# Multilingüe (buen desempeño en español), 384 dimensiones y ~220 MB: cabe
# en un contenedor chico y no necesita GPU ni una API externa de embeddings.
MODELO_EMBEDDINGS = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONES = 384

# Metadatos como columnas reales (no solo JSON) para poder filtrar por SQL.
COLUMNAS_METADATOS = ["titulo", "fuente", "url", "archivo", "seccion"]


class EmbeddingsLocales(Embeddings):
    """Adaptador de fastembed (ONNX, sin PyTorch) a la interfaz de LangChain."""

    def __init__(self, modelo: str = MODELO_EMBEDDINGS):
        # FASTEMBED_CACHE_PATH permite fijar dónde se descarga el modelo
        # (útil en Docker para hornearlo en la imagen).
        self._modelo = TextEmbedding(modelo, cache_dir=os.getenv("FASTEMBED_CACHE_PATH"))

    def embed_documents(self, textos: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._modelo.embed(textos)]

    def embed_query(self, texto: str) -> list[float]:
        return self.embed_documents([texto])[0]


@lru_cache(maxsize=1)
def obtener_embeddings() -> EmbeddingsLocales:
    return EmbeddingsLocales()


@lru_cache(maxsize=1)
def obtener_pg_engine() -> PGEngine:
    """PGEngine corre sus consultas async en un event loop de un hilo aparte.
    Le damos uno propio de tipo Selector: el loop por defecto de Windows
    (Proactor) no es compatible con psycopg en modo async."""
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector()) if sys.platform == "win32" else asyncio.new_event_loop()
    Thread(target=loop.run_forever, daemon=True).start()
    return PGEngine.from_engine(create_async_engine(obtener_url("psycopg")), loop)


def crear_tabla() -> None:
    """Crea (o recrea desde cero) la tabla de vectores."""
    obtener_pg_engine().init_vectorstore_table(
        TABLA,
        DIMENSIONES,
        metadata_columns=[Column(c, "TEXT") for c in COLUMNAS_METADATOS],
        overwrite_existing=True,
    )


@lru_cache(maxsize=1)
def obtener_vectorstore() -> PGVectorStore:
    return PGVectorStore.create_sync(
        obtener_pg_engine(),
        obtener_embeddings(),
        TABLA,
        metadata_columns=COLUMNAS_METADATOS,
    )


def leer_documento(ruta: Path) -> tuple[dict, str]:
    """Separa el encabezado YAML (titulo, fuente, url) del cuerpo markdown."""
    # Git en Windows puede hacer checkout con CRLF; normalizamos a LF.
    texto = ruta.read_text(encoding="utf-8").replace("\r\n", "\n")
    coincidencia = re.match(r"^---\n(.*?)\n---\n(.*)$", texto, re.DOTALL)
    if not coincidencia:
        raise ValueError(f"{ruta.name}: falta el encabezado YAML entre líneas '---'")
    return yaml.safe_load(coincidencia.group(1)), coincidencia.group(2)


def dividir_documento(ruta: Path) -> list[Document]:
    """Un fragmento por sección del markdown. Cada fragmento lleva el título
    del documento al inicio: una sección como "## Antes: preparación" no dice
    por sí sola que habla de sismos, y sin ese contexto la búsqueda falla."""
    meta, cuerpo = leer_documento(ruta)
    divisor = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    fragmentos = []
    for seccion in divisor.split_text(cuerpo):
        nombre_seccion = seccion.metadata.get("h3") or seccion.metadata.get("h2") or seccion.metadata.get("h1")
        fragmentos.append(Document(
            page_content=f"Documento: {meta['titulo']}\n\n{seccion.page_content}",
            metadata={
                "titulo": meta["titulo"],
                "fuente": meta["fuente"],
                "url": meta.get("url"),
                "archivo": ruta.name,
                "seccion": nombre_seccion,
            },
        ))
    return fragmentos


def cargar_conocimiento() -> int:
    """Recrea la tabla e indexa todos los documentos. Devuelve cuántos
    fragmentos se indexaron."""
    fragmentos = [f for ruta in sorted(CARPETA_CONOCIMIENTO.glob("*.md")) for f in dividir_documento(ruta)]
    crear_tabla()
    obtener_vectorstore.cache_clear()
    obtener_vectorstore().add_documents(fragmentos)
    return len(fragmentos)


def buscar(pregunta: str, k: int = 4) -> list[dict]:
    """Los k fragmentos más parecidos a la pregunta, con su fuente."""
    resultados = obtener_vectorstore().similarity_search_with_score(pregunta, k=k)
    return [
        {
            "texto": doc.page_content,
            "titulo": doc.metadata["titulo"],
            "seccion": doc.metadata["seccion"],
            "fuente": doc.metadata["fuente"],
            "url": doc.metadata["url"],
            # El score es distancia coseno (0 = idéntico); la pasamos a similitud.
            "similitud": round(1 - float(distancia), 3),
        }
        for doc, distancia in resultados
    ]
