"""Shared test fixtures.

Tests run against a throwaway SQLite file by default. Set TEST_DATABASE_URL to run the same
suite against a disposable Postgres (tables are dropped and recreated for every test).
"""

import os
import tempfile
from collections.abc import Iterator

# Configure the environment before the app (and its engine) is imported.
_db_dir = tempfile.mkdtemp(prefix="patient-intake-tests-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_db_dir}/test.db"
os.environ["API_KEY"] = "test-api-key"
os.environ["VAPI_WEBHOOK_SECRET"] = "test-vapi-secret"
os.environ["SEED_ON_STARTUP"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """A test client backed by an empty database."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


AUTH = {"X-API-Key": "test-api-key"}

VALID_PATIENT = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "03/04/1990",
    "sex": "Female",
    "phone_number": "(212) 555-0123",
    "address_line_1": "123 Main St",
    "address_line_2": "Apt 4B",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
}


def assert_envelope(body: dict) -> None:
    """Every response has exactly the keys data and error, and exactly one of them is set."""
    assert set(body) == {"data", "error"}
    assert (body["data"] is None) != (body["error"] is None)


@pytest.fixture()
def create_patient(client: TestClient):
    """Factory: create a patient through the API and return its JSON."""

    def _create(**overrides: object) -> dict:
        response = client.post("/patients", json=VALID_PATIENT | overrides, headers=AUTH)
        assert response.status_code == 201, response.text
        return response.json()["data"]

    return _create
