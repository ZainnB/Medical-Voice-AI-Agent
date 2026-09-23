from fastapi.testclient import TestClient

from app.db import normalize_database_url


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["db_engine"] in {"sqlite", "postgresql"}
    assert response.json()["error"] is None


def test_database_url_normalization() -> None:
    assert normalize_database_url("") == "sqlite:///./local.db"
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///./x.db") == "sqlite:///./x.db"
