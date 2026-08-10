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