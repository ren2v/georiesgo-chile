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


# Buscar la palabra suelta daría falsos positivos ("bajo la placa
# Sudamericana", "evacúa a terreno alto"), así que un nivel cuenta solo si:
# - va con mayúscula (como lo escribe el modelo al citar el resultado) en la
#   misma oración que "riesgo"/"nivel": "El nivel de riesgo sísmico relativo
#   en la Plaza de Armas de Santiago es **Moderado**";
# - o en minúscula, pegado a "riesgo"/"nivel": "tiene riesgo alto".
PATRONES_NIVEL = [
    re.compile(r"\b(?i:riesgo|nivel)\b[^.\n]{0,80}?\b(Alto|Moderado|Bajo|ALTO|MODERADO|BAJO)\b"),
    re.compile(r"\b(?:riesgo|nivel)\W+(?:\w+\W+){0,2}?(alto|moderado|bajo)\b", re.IGNORECASE),
]


def nombrados(texto: str) -> set[str]:
    """Niveles de riesgo que la respuesta atribuye al lugar."""
    return {m.group(1).capitalize() for patron in PATRONES_NIVEL for m in patron.finditer(texto)}


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
        # Con la cadena de respaldo, cada caso puede responderlo un modelo distinto.
        "modelo_respuesta": resultado["modelo"],
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
    argumentos = sys.argv[1:]
    anteriores = {}
    salida = None
    if "--reintentar" in argumentos:
        # Solo los casos que en la última corrida dieron error de servicio
        # (modelo caído, cuota): los ya evaluados no se repiten ni gastan cuota.
        salida = max(RESULTADOS.glob("*.json"), key=lambda p: p.stat().st_mtime)
        anteriores = {r["id"]: r for r in json.loads(salida.read_text(encoding="utf-8"))["resultados"]}
        pendientes = {i for i, r in anteriores.items() if "error" in r}
        casos = [c for c in casos if c["id"] in pendientes]
        print(f"Reintentando {len(casos)} caso(s) con error de {salida.name}")
    elif filtro := set(argumentos):
        casos = [c for c in casos if c["id"] in filtro]

    print(f"Modelo: {agente.MODELO} (respaldo: {', '.join(agente.MODELOS_RESPALDO) or 'ninguno'}) · {len(casos)} casos\n")
    for i, caso in enumerate(casos):
        if i:
            time.sleep(PAUSA_ENTRE_CASOS_S)
        r = correr_caso(caso)
        anteriores[r["id"]] = r
        if "error" in r:
            print(f"ERROR {r['id']}\n       ✗ {r['error'][:200]}")
            continue
        print(f"{'OK  ' if r['ok'] else 'FALLA'} {r['id']}  [{r['modelo_respuesta']}]")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"       ✗ {c['check']} {c['detalle']}")

    resultados = list(anteriores.values())
    # Los casos con error de servicio no se evaluaron: no cuentan como falla
    # del agente, se informan aparte para no mezclar calidad con disponibilidad.
    evaluados = [r for r in resultados if "error" not in r]
    resumen = {
        "casos_ok": sum(r["ok"] for r in evaluados),
        "casos_evaluados": len(evaluados),
        "casos_con_error_de_servicio": len(resultados) - len(evaluados),
        "checks_ok": sum(c["ok"] for r in evaluados for c in r["checks"]),
        "checks_total": sum(len(r["checks"]) for r in evaluados),
    }
    print(f"\nCasos: {resumen['casos_ok']}/{resumen['casos_evaluados']} · "
          f"Checks: {resumen['checks_ok']}/{resumen['checks_total']} · "
          f"Sin evaluar por error de servicio: {resumen['casos_con_error_de_servicio']}")

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    if salida is None:
        modelo = agente.MODELO.replace(":", "_").replace("/", "_")
        salida = RESULTADOS / f"{datetime.now():%Y-%m-%d_%H%M}_{modelo}.json"
    salida.write_text(json.dumps({
        "modelo": agente.MODELO,
        "modelos_respaldo": agente.MODELOS_RESPALDO,
        **resumen,
        "resultados": resultados,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Detalle en {salida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
