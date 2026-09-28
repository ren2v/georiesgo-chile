"""Corre los casos de evals/casos.yaml contra el agente real y guarda el
resultado en evals/resultados/. Cada corrida consume cuota del modelo
(unas 3 llamadas por caso).

    python backend/agente/evaluar.py                 # todos los casos
    python backend/agente/evaluar.py que_es_citsu    # solo algunos
"""
import json
import re
import sys
import time
import unicodedata
import uuid
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

import yaml  # noqa: E402

from agente import agente  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
CASOS = RAIZ / "evals" / "casos.yaml"
RESULTADOS = RAIZ / "evals" / "resultados"

# Pausa entre casos para no chocar con el límite de peticiones por minuto
# del tier gratuito.
PAUSA_ENTRE_CASOS_S = 8

NIVELES = ["Alto", "Moderado", "Bajo"]


def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: "Evacúa" y "evacua" deben calzar igual."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def contiene(texto: str, frase: str) -> bool:
    return normalizar(frase) in normalizar(texto)


# Un nivel cuenta solo si va tras "riesgo" o "nivel" (con hasta 3 palabras
# entre medio: "riesgo sísmico relativo **Alto**"). Buscar la palabra suelta
# daría falsos positivos: "bajo la placa Sudamericana", "terreno alto".
PATRON_NIVEL = re.compile(r"\b(?:riesgo|nivel)\W+(?:\w+\W+){0,3}?(alto|moderado|bajo)\b", re.IGNORECASE)


def nombrados(texto: str) -> set[str]:
    """Niveles de riesgo que la respuesta atribuye al lugar."""
    return {m.group(1).capitalize() for m in PATRON_NIVEL.finditer(texto)}


def verificar(caso: dict, resultado: dict) -> list[dict]:
    respuesta = resultado["respuesta"]
    usadas = [h["nombre"] for h in resultado["herramientas"]]
    checks = []

    def check(nombre, ok, detalle=""):
        checks.append({"check": nombre, "ok": bool(ok), "detalle": detalle})

    if esperadas := caso.get("herramientas_esperadas"):
        # Subsecuencia: en ese orden, pero puede haber otras llamadas entre medio.
        restantes = iter(usadas)
        check("herramientas_esperadas", all(h in restantes for h in esperadas), f"usó {usadas}")

    if caso.get("sin_herramientas"):
        check("sin_herramientas", not usadas, f"usó {usadas}")

    for prohibida in caso.get("no_debe_usar", []):
        check(f"no_usa:{prohibida}", prohibida not in usadas, f"usó {usadas}")

    for grupo in caso.get("debe_mencionar", []):
        check(f"menciona:{'|'.join(grupo)}", any(contiene(respuesta, f) for f in grupo))

    for frase in caso.get("no_debe_mencionar", []):
        check(f"no_menciona:{frase}", not contiene(respuesta, frase))

    if caso.get("nivel_consistente"):
        evaluaciones = [h["resultado"] for h in resultado["herramientas"]
                        if h["nombre"] == "evaluar_riesgo_sismico" and isinstance(h["resultado"], dict)]
        niveles_reales = {e["nivel_riesgo"] for e in evaluaciones if "nivel_riesgo" in e}
        en_respuesta = nombrados(respuesta)
        check(
            "nivel_consistente",
            niveles_reales and niveles_reales <= en_respuesta and not (en_respuesta - niveles_reales),
            f"herramienta: {sorted(niveles_reales)} · respuesta: {sorted(en_respuesta)}",
        )

    return checks


def correr_caso(caso: dict) -> dict:
    conversacion_id = f"eval-{caso['id']}-{uuid.uuid4().hex[:8]}"
    inicio = time.perf_counter()
    try:
        for turno in caso["turnos"]:
            resultado = agente.preguntar(turno, conversacion_id)
    except Exception as e:
        return {"id": caso["id"], "error": f"{type(e).__name__}: {e}", "checks": [], "ok": False}
    checks = verificar(caso, resultado)
    return {
        "id": caso["id"],
        "segundos": round(time.perf_counter() - inicio, 1),
        "respuesta": resultado["respuesta"],
        "herramientas": [{"nombre": h["nombre"], "args": h["args"]} for h in resultado["herramientas"]],
        "checks": checks,
        "ok": all(c["ok"] for c in checks),
    }


def main():
    if not agente.llave_configurada():
        sys.exit(f"Falta la API key para {agente.MODELO} en el archivo .env")

    casos = yaml.safe_load(CASOS.read_text(encoding="utf-8"))
    if filtro := set(sys.argv[1:]):
        casos = [c for c in casos if c["id"] in filtro]

    print(f"Modelo: {agente.MODELO} · {len(casos)} casos\n")
    resultados = []
    for i, caso in enumerate(casos):
        if i:
            time.sleep(PAUSA_ENTRE_CASOS_S)
        r = correr_caso(caso)
        resultados.append(r)
        marca = "OK  " if r["ok"] else "FALLA"
        print(f"{marca} {r['id']}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       ✗ {c['check']} {c['detalle']}")
        if "error" in r:
            print(f"       ✗ {r['error'][:200]}")

    total_checks = sum(len(r["checks"]) for r in resultados)
    checks_ok = sum(c["ok"] for r in resultados for c in r["checks"])
    casos_ok = sum(r["ok"] for r in resultados)
    print(f"\nCasos: {casos_ok}/{len(resultados)} · Checks: {checks_ok}/{total_checks}")

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    marca_tiempo = datetime.now().strftime("%Y-%m-%d_%H%M")
    modelo = agente.MODELO.replace(":", "_").replace("/", "_")
    salida = RESULTADOS / f"{marca_tiempo}_{modelo}.json"
    salida.write_text(json.dumps({
        "modelo": agente.MODELO,
        "fecha": marca_tiempo,
        "casos_ok": casos_ok,
        "casos_total": len(resultados),
        "checks_ok": checks_ok,
        "checks_total": total_checks,
        "resultados": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detalle en {salida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
