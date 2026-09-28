"""Chat con el agente desde la terminal, para probarlo sin frontend:

    python backend/agente/consola.py
"""
import sys
import uuid
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from agente import agente  # noqa: E402


def main():
    if not agente.llave_configurada():
        sys.exit(f"Falta la API key para {agente.MODELO}: agrégala al archivo .env en la raíz del repo.")
    conversacion_id = str(uuid.uuid4())
    print(f"GeoRiesgo Chile · {agente.MODELO} · escribe 'salir' para terminar\n")
    while (mensaje := input("tú> ").strip()).lower() not in {"salir", "exit"}:
        if not mensaje:
            continue
        resultado = agente.preguntar(mensaje, conversacion_id)
        for h in resultado["herramientas"]:
            print(f"  · {h['nombre']}({h['args']})")
        print(f"\nagente> {resultado['respuesta']}\n")


if __name__ == "__main__":
    main()
