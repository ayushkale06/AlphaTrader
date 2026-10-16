import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from src.gateway.app import app

client = TestClient(app)


def test_cors_headers_present() -> None:
    response = client.options(
        "/api/v1/portfolio/summary",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_prohibit_post_mutations() -> None:
    # Any POST method on /api/v1/orders must be rejected with 405 Method Not Allowed
    response = client.post("/api/v1/orders", json={"symbol": "RELIANCE.NS"})
    assert response.status_code == 405


def test_health_endpoint() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["trading_mode"] == "PAPER"
    assert "services" in data
