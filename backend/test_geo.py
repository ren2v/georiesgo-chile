import math
import numpy as np
import pytest
import geo


def test_normalizar_distancia_falla_en_la_falla():
    assert geo.normalizar_distancia_falla(0) == 1.0


def test_normalizar_distancia_falla_en_el_limite():
    assert geo.normalizar_distancia_falla(geo.RADIO_INFLUENCIA_FALLA_KM) == 0.0


def test_normalizar_distancia_falla_mas_alla_del_limite_no_es_negativo():
    assert geo.normalizar_distancia_falla(geo.RADIO_INFLUENCIA_FALLA_KM * 2) == 0.0


def test_normalizar_distancia_falla_punto_medio():
    mitad = geo.RADIO_INFLUENCIA_FALLA_KM / 2
    assert geo.normalizar_distancia_falla(mitad) == pytest.approx(0.5)


def test_normalizar_exposicion_costa_en_la_costa():
    assert geo.normalizar_exposicion_costa(0) == 1.0


def test_normalizar_exposicion_costa_en_el_limite():
    assert geo.normalizar_exposicion_costa(geo.RADIO_EXPOSICION_COSTA_KM) == 0.0


def test_normalizar_exposicion_costa_mas_alla_del_limite_no_es_negativo():
    assert geo.normalizar_exposicion_costa(geo.RADIO_EXPOSICION_COSTA_KM * 3) == 0.0


def test_normalizar_magnitud_en_la_base_es_cero():
    assert geo.normalizar_magnitud(4.0, base=4.0) == 0.0


def test_normalizar_magnitud_en_el_techo_es_uno():
    assert geo.normalizar_magnitud(7.0, base=4.0) == 1.0


def test_normalizar_magnitud_bajo_la_base_no_es_negativo():
    assert geo.normalizar_magnitud(2.0, base=4.0) == 0.0


def test_normalizar_magnitud_sobre_el_techo_no_pasa_de_uno():
    assert geo.normalizar_magnitud(9.0, base=4.0) == 1.0


def test_construir_factor_calcula_contribucion_correctamente():
    factor = geo.construir_factor("suelo", normalizado=0.5, peso_maximo=2, texto="prueba")
    assert factor["contribucion"] == 1.0
    assert factor["peso_maximo"] == 2
    assert factor["categoria"] == "suelo"


def test_score_pct_siempre_entre_0_y_100():
    puntos = [
        (-33.45, -70.65),  # Santiago
        (-23.65, -70.40),  # Antofagasta
        (-22.91, -68.20),  # San Pedro de Atacama
        (-53.16, -70.91),  # Punta Arenas, extremo sur
    ]
    for lat, lng in puntos:
        resultado = geo.evaluar_riesgo(lat, lng)
        assert 0 <= resultado["score_pct"] <= 100


def test_evaluar_riesgo_devuelve_nivel_valido():
    resultado = geo.evaluar_riesgo(-33.45, -70.65)
    assert resultado["nivel_riesgo"] in ("Bajo", "Moderado", "Alto")


def test_mas_cerca_de_la_falla_implica_mayor_normalizado():
    lejos = geo.normalizar_distancia_falla(25)
    cerca = geo.normalizar_distancia_falla(5)
    assert cerca > lejos


def test_consultar_geologia_en_el_oceano_no_encuentra_datos():
    resultado = geo.consultar_geologia(-33.0, -80.0)
    assert resultado["encontrado"] is False


def test_consultar_fallas_cercanas_ordenadas_por_distancia():
    fallas = geo.consultar_fallas_cercanas(-33.45, -70.54, radio_km=50)
    distancias = [f["distancia_km"] for f in fallas]
    assert distancias == sorted(distancias)


def test_consultar_sismos_filtra_por_profundidad():
    corticales = geo.consultar_sismos_cercanos(
        -33.45, -70.65, radio_km=100, min_magnitud=4.0, profundidad_max=30
    )
    assert all(s["profundidad_km"] <= 30 for s in corticales)


def test_consultar_distancia_costa_punto_costero_menor_que_inland():
    dist_costero = geo.consultar_distancia_costa(-33.03, -71.63)   # Valparaíso, en la costa
    dist_inland = geo.consultar_distancia_costa(-22.91, -68.20)    # San Pedro de Atacama
    assert dist_costero < dist_inland


def test_consultar_sismos_historicos_encuentra_valdivia_1960_cerca_de_valdivia():
    eventos = geo.consultar_sismos_historicos_cercanos(-39.8142, -73.2459)
    nombres = [e["nombre"] for e in eventos]
    assert "Valdivia 1960" in nombres


def test_consultar_sismos_historicos_no_encuentra_nada_lejos_de_la_costa():
    eventos = geo.consultar_sismos_historicos_cercanos(-22.91, -68.20)  # San Pedro, interior
    assert eventos == []


def test_valdivia_captura_el_terremoto_de_1960_con_exposicion_alta():
    # El caso que motivó todo este rediseño: Valdivia está a distancia corta
    # del epicentro de 1960 (M9.5, el mayor sismo jamás registrado). Con la
    # fórmula de alivio ponderada por magnitud, esto vuelve a clasificar
    # "Alto" — no porque forzáramos ese resultado, sino como consecuencia de
    # corregir un problema conceptual real (un M6.0 no debía "resetear" la
    # urgencia igual que un M9.5). Si esto deja de cumplirse tras un cambio,
    # investigar antes de simplemente subir el número.
    resultado = geo.evaluar_riesgo(-39.8142, -73.2459)
    assert resultado["nivel_riesgo"] == "Alto"
    factor_exposicion = next(f for f in resultado["factores"] if f["categoria"] == "exposicion_subduccion")
    assert factor_exposicion["normalizado"] >= 0.85


def test_piedemonte_es_alto_por_falla_extrema():
    # Punto a ~2km de la Falla San Ramón — debe seguir siendo "Alto" por su
    # propia vía (falla extrema), sin depender del factor de subducción.
    resultado = geo.evaluar_riesgo(-33.45, -70.54)
    assert resultado["nivel_riesgo"] == "Alto"
    factor_falla = next(f for f in resultado["factores"] if f["categoria"] == "falla")
    assert factor_falla["normalizado"] >= geo.UMBRAL_FACTOR_EXTREMO


def test_san_pedro_no_activa_anulacion_por_amenaza_extrema():
    # Punto sin falla cercana y lejos de la costa — no debería activar la
    # anulación por amenaza extrema aislada, y debe quedar en "Bajo".
    resultado = geo.evaluar_riesgo(-22.91, -68.20)
    assert resultado["nivel_riesgo"] == "Bajo"
    categorias = [f["categoria"] for f in resultado["factores"]]
    assert "anulacion_alto" not in categorias


# ---------------------------------------------------------------------------
# Regresión: NaN de pandas colándose en la respuesta JSON.
# ---------------------------------------------------------------------------

def _contiene_nan(obj):
    if isinstance(obj, float):
        return math.isnan(obj)
    if isinstance(obj, dict):
        return any(_contiene_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contiene_nan(v) for v in obj)
    return False


def test_evaluar_riesgo_no_contiene_nan_region_biobio():
    resultado = geo.evaluar_riesgo(-37.097048852585345, -72.49674767725125)
    assert not _contiene_nan(resultado)


def test_consultar_geologia_no_contiene_nan_en_varios_puntos():
    puntos = [
        (-18.47, -70.30),
        (-33.45, -70.65),
        (-37.10, -72.50),
        (-41.47, -72.94),
        (-53.16, -70.91),
    ]
    for lat, lng in puntos:
        resultado = geo.consultar_geologia(lat, lng)
        assert not _contiene_nan(resultado)


def test_evaluar_riesgo_no_contiene_nan_casos_reportados():
    puntos = [
        (-37.92157216245872, -71.75560902325236),
        (-31.73544126985597, -70.67270027583997),
    ]
    for lat, lng in puntos:
        resultado = geo.evaluar_riesgo(lat, lng)
        assert not _contiene_nan(resultado)


def test_evaluar_riesgo_no_contiene_nan_en_grid_nacional():
    lats = np.linspace(-55, -18, 8)
    lngs = np.linspace(-75, -67, 4)
    for lat in lats:
        for lng in lngs:
            resultado = geo.evaluar_riesgo(float(lat), float(lng))
            assert not _contiene_nan(resultado), f"NaN encontrado en ({lat}, {lng})"


def test_geologia_sin_nan_tras_limpiar_en_todas_las_filas():
    columnas = ["ambiente", "periodos", "litoestratos", "litologia", "roca1", "roca2", "roca3", "roca4"]
    for columna in columnas:
        valores_limpios = geo.geologia[columna].apply(geo.limpiar)
        assert not any(
            isinstance(v, float) and math.isnan(v) for v in valores_limpios
        ), f"Quedó un NaN sin limpiar en la columna '{columna}'"


def test_fallas_sin_nan_tras_limpiar_en_todas_las_filas():
    for columna in ["name", "slip_type"]:
        valores_limpios = geo.fallas[columna].apply(geo.limpiar)
        assert not any(
            isinstance(v, float) and math.isnan(v) for v in valores_limpios
        ), f"Quedó un NaN sin limpiar en la columna '{columna}'"
