"""Indexa la carpeta conocimiento/ en pgvector. Correr cada vez que se
agregue o edite un documento:

    python backend/agente/ingesta.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente import rag  # noqa: E402


def main():
    inicio = time.perf_counter()
    total = rag.cargar_conocimiento()
    print(f"{total} fragmentos indexados en la tabla '{rag.TABLA}' en {time.perf_counter() - inicio:.1f}s")


if __name__ == "__main__":
    main()
