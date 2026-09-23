"""Shared test fixtures. Tests run against a throwaway SQLite file, never a real database."""

import os
import tempfile
from collections.abc import Iterator

# Configure the environment before the app (and its engine) is imported.
_db_dir = tempfile.mkdtemp(prefix="patient-intake-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_dir}/test.db"
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
