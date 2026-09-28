"""Conversión de filas crudas (de GeoPandas o de PostGIS) al formato de
respuesta de la API. Vive aparte de geo.py para que ambos backends de datos
compartan exactamente la misma salida — `fila.get(...)` funciona igual sobre
una fila de pandas que sobre un RowMapping de SQLAlchemy."""
import pandas as pd

EXPANSION_ROCA = {
    "metareni": "metareniscas",
    "monzodio": "monzodiorita",
    "metapeli": "metapelitas",
    "metasedi": "metasedimentos",
    "granodio": "granodiorita",
}


def limpiar(valor):
    """Convierte cualquier variante de NaN/None/NA de pandas a None. Necesario
    porque columnas con datos faltantes pueden devolver distintos tipos de
    'vacío' (numpy.float64 nan, numpy.float32 nan, pandas.NA) según cómo se
    haya inferido el tipo de esa columna al leer el archivo — y NaN no es
    válido en JSON estricto (rompe la respuesta con 500 en producción)."""
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    return valor


def expandir(codigo):
    codigo = limpiar(codigo)
    if codigo is None:
        return None
    return EXPANSION_ROCA.get(codigo, codigo)


def formatear_geologia(fila) -> dict:
    rocas = [expandir(fila.get(f"roca{i}")) for i in range(1, 5)]
    rocas = [r for r in rocas if r]

    return {
        "encontrado": True,
        "ambiente": limpiar(fila.get("ambiente")),
        "periodo": limpiar(fila.get("periodos")),
        "rocas_dominantes": rocas,
        "litoestratos": limpiar(fila.get("litoestratos")),
        "descripcion": limpiar(fila.get("litologia")),
    }


def formatear_falla(fila) -> dict:
    return {
        "nombre": limpiar(fila.get("name")) or "Sin nombre catalogado",
        "distancia_km": round(float(fila["distancia_km"]), 1),
        "tipo_movimiento": limpiar(fila.get("slip_type")),
    }


def formatear_sismo(fila) -> dict:
    return {
        "fecha": fila["fecha"],
        "magnitud": limpiar(fila["magnitud"]),
        "profundidad_km": limpiar(fila["profundidad_km"]),
        "distancia_km": round(float(fila["distancia_km"]), 1),
    }
