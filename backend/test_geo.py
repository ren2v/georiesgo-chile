import math
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
    # A igualdad de otros factores, estar más cerca de una falla nunca debería
    # dar un normalizado menor — es la propiedad que hace que el modelo tenga
    # sentido físico, no solo que "dé resultados razonables" en los puntos que probamos.
    lejos = geo.normalizar_distancia_falla(25)
    cerca = geo.normalizar_distancia_falla(5)
    assert cerca > lejos


def test_consultar_geologia_en_el_oceano_no_encuentra_datos():
    # Punto en el Pacífico, lejos de la costa — no debería haber polígono geológico
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


# ---------------------------------------------------------------------------
# Regresión: bug de NaN rompiendo la serialización JSON en producción
# ---------------------------------------------------------------------------

def _contiene_nan(obj):
    """Recorre recursivamente dicts/listas buscando algún float NaN, que
    rompería la serialización JSON con allow_nan=False (como hace FastAPI)."""
    if isinstance(obj, float):
        return math.isnan(obj)
    if isinstance(obj, dict):
        return any(_contiene_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contiene_nan(v) for v in obj)
    return False


def test_evaluar_riesgo_no_contiene_nan_region_biobio():
    # Regresión: este punto (Región del Biobío) rompía la respuesta en
    # producción porque un polígono geológico sin roca3/roca4 devolvía
    # NaN de pandas en vez de None, lo cual no es válido en JSON.
    resultado = geo.evaluar_riesgo(-37.097048852585345, -72.49674767725125)
    assert not _contiene_nan(resultado)


def test_consultar_geologia_no_contiene_nan_en_varios_puntos():
    puntos = [
        (-18.47, -70.30),   # Arica, extremo norte
        (-33.45, -70.65),   # Santiago
        (-37.10, -72.50),   # Biobío (punto que causó el bug)
        (-41.47, -72.94),   # Puerto Montt
        (-53.16, -70.91),   # Punta Arenas
    ]
    for lat, lng in puntos:
        resultado = geo.consultar_geologia(lat, lng)
        assert not _contiene_nan(resultado)