from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_raiz_responde_ok():
    resp = client.get("/")
    assert resp.status_code == 200


def test_riesgo_dentro_de_chile_responde_ok():
    resp = client.get("/riesgo", params={"lat": -33.45, "lng": -70.65})
    assert resp.status_code == 200
    data = resp.json()
    assert "nivel_riesgo" in data
    assert "score_pct" in data


def test_riesgo_fuera_de_chile_responde_400():
    resp = client.get("/riesgo", params={"lat": 40.0, "lng": -3.0})  # Madrid
    assert resp.status_code == 400


def test_riesgo_requiere_parametros():
    resp = client.get("/riesgo")
    assert resp.status_code == 422  # FastAPI valida automáticamente


# ---------------------------------------------------------------------------
# Elevación: se mockea la llamada externa en todos los casos — los tests no
# deben depender de que una API de terceros esté disponible para pasar.
# ---------------------------------------------------------------------------

def test_riesgo_agrega_elevacion_en_punto_costero_cuando_la_api_responde():
    with patch("main.consultar_elevacion", return_value=12.5):
        resp = client.get("/riesgo", params={"lat": -33.03, "lng": -71.63})  # Valparaíso, costero
    assert resp.status_code == 200
    data = resp.json()
    assert data["elevacion_m"] == 12.5


def test_riesgo_no_rompe_si_la_api_de_elevacion_falla():
    # Degradación elegante: si la API externa falla, la respuesta sigue
    # siendo 200 con datos completos, solo sin el dato de elevación.
    with patch("main.consultar_elevacion", return_value=None):
        resp = client.get("/riesgo", params={"lat": -33.03, "lng": -71.63})
    assert resp.status_code == 200
    data = resp.json()
    assert data["elevacion_m"] is None
    assert "nivel_riesgo" in data  # el resto de la respuesta sigue intacto


def test_riesgo_no_consulta_elevacion_lejos_de_la_costa():
    # No debería gastar una llamada externa si el punto no es costero.
    with patch("main.consultar_elevacion") as mock_elevacion:
        resp = client.get("/riesgo", params={"lat": -22.91, "lng": -68.20})  # San Pedro, interior
    assert resp.status_code == 200
    mock_elevacion.assert_not_called()
