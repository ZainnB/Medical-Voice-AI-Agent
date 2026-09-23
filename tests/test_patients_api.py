import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal
from app.seed import DEMO_PATIENTS, seed_demo_patients
from app.services import patients as patient_service
from tests.conftest import AUTH, VALID_PATIENT, assert_envelope


# ---- Create ---------------------------------------------------------------------------------

def test_create_returns_201_with_normalized_record(client: TestClient) -> None:
    response = client.post("/patients", json=VALID_PATIENT | {"state": "texas", "sex": "f"}, headers=AUTH)
    assert response.status_code == 201
    body = response.json()
    assert_envelope(body)
    data = body["data"]
    uuid.UUID(data["patient_id"])
    assert data["phone_number"] == "2125550123"
    assert data["state"] == "TX"
    assert data["sex"] == "Female"
    assert data["date_of_birth"] == "03/04/1990"
    assert data["preferred_language"] == "English"
    assert data["deleted_at"] is None
    assert data["created_at"].endswith("+00:00")


def test_create_missing_required_fields_returns_422_with_details(client: TestClient) -> None:
    response = client.post("/patients", json={"first_name": "Jane"}, headers=AUTH)
    assert response.status_code == 422
    body = response.json()
    assert_envelope(body)
    assert body["error"]["code"] == "validation_error"
    fields = {d["field"] for d in body["error"]["details"]}
    assert {"last_name", "date_of_birth", "phone_number", "zip_code"} <= fields


def test_create_invalid_values_return_field_messages(client: TestClient) -> None:
    response = client.post(
        "/patients", json=VALID_PATIENT | {"date_of_birth": "01/01/2999", "phone_number": "555-0123"}, headers=AUTH
    )
    assert response.status_code == 422
    details = {d["field"]: d["message"] for d in response.json()["error"]["details"]}
    assert details == {
        "date_of_birth": "Date of birth can't be in the future",
        "phone_number": "Phone number must be a 10-digit US number",
    }


def test_create_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post("/patients", json=VALID_PATIENT | {"ssn": "123-45-6789"}, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "ssn"


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_create_requires_api_key(client: TestClient, headers: dict) -> None:
    response = client.post("/patients", json=VALID_PATIENT, headers=headers)
    assert response.status_code == 401
    assert_envelope(response.json())
    assert response.json()["error"]["code"] == "unauthorized"


def test_malformed_json_returns_400(client: TestClient) -> None:
    response = client.post(
        "/patients", content=b'{"first_name": ', headers=AUTH | {"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_json"


# ---- Read -----------------------------------------------------------------------------------

def test_get_by_id(client: TestClient, create_patient) -> None:
    patient = create_patient()
    response = client.get(f"/patients/{patient['patient_id']}")
    assert response.status_code == 200
    assert response.json()["data"] == patient


def test_get_malformed_uuid_returns_400(client: TestClient) -> None:
    response = client.get("/patients/not-a-uuid")
    assert response.status_code == 400
    assert_envelope(response.json())


def test_get_unknown_returns_404(client: TestClient) -> None:
    response = client.get(f"/patients/{uuid.uuid4()}")
    assert response.status_code == 404
    assert_envelope(response.json())
    assert response.json()["error"]["code"] == "not_found"


def test_list_filters(client: TestClient, create_patient) -> None:
    jane = create_patient()
    create_patient(first_name="Bob", last_name="Smith", phone_number="3125550199", date_of_birth="1970-01-01")

    def ids(params: dict) -> list[str]:
        response = client.get("/patients", params=params)
        assert response.status_code == 200, response.text
        assert_envelope(response.json())
        return [p["patient_id"] for p in response.json()["data"]]

    assert len(ids({})) == 2
    assert ids({"last_name": "DOE"}) == [jane["patient_id"]]
    assert ids({"phone_number": "+1 (212) 555-0123"}) == [jane["patient_id"]]
    assert ids({"date_of_birth": "03/04/1990"}) == [jane["patient_id"]]
    assert ids({"date_of_birth": "1990-03-04"}) == [jane["patient_id"]]
    assert ids({"last_name": "nobody"}) == []
    assert len(ids({"limit": 1})) == 1


@pytest.mark.parametrize(
    "params", [{"phone_number": "123"}, {"date_of_birth": "yesterday"}, {"limit": 500}, {"offset": -1}]
)
def test_list_bad_query_param_returns_400(client: TestClient, params: dict) -> None:
    response = client.get("/patients", params=params)
    assert response.status_code == 400
    assert_envelope(response.json())


# ---- Update ---------------------------------------------------------------------------------

def test_put_updates_only_given_fields_and_bumps_updated_at(client: TestClient, create_patient) -> None:
    patient = create_patient()
    response = client.put(
        f"/patients/{patient['patient_id']}", json={"city": "Dallas", "zip_code": "75201"}, headers=AUTH
    )
    assert response.status_code == 200
    updated = response.json()["data"]
    assert updated["city"] == "Dallas"
    assert updated["zip_code"] == "75201"
    assert updated["updated_at"] > patient["updated_at"]
    unchanged = {k: v for k, v in updated.items() if k not in {"city", "zip_code", "updated_at"}}
    assert unchanged == {k: v for k, v in patient.items() if k not in {"city", "zip_code", "updated_at"}}


def test_put_can_clear_optional_field(client: TestClient, create_patient) -> None:
    patient = create_patient()
    response = client.put(f"/patients/{patient['patient_id']}", json={"address_line_2": None}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["data"]["address_line_2"] is None


@pytest.mark.parametrize(
    "body", [{"zip_code": "1"}, {"last_name": None}, {}, {"unknown_field": "x"}, {"date_of_birth": "12/31/2999"}]
)
def test_put_invalid_returns_422(client: TestClient, create_patient, body: dict) -> None:
    patient = create_patient()
    response = client.put(f"/patients/{patient['patient_id']}", json=body, headers=AUTH)
    assert response.status_code == 422
    assert_envelope(response.json())


def test_put_unknown_patient_returns_404(client: TestClient) -> None:
    response = client.put(f"/patients/{uuid.uuid4()}", json={"city": "Dallas"}, headers=AUTH)
    assert response.status_code == 404


def test_put_requires_api_key(client: TestClient, create_patient) -> None:
    patient = create_patient()
    assert client.put(f"/patients/{patient['patient_id']}", json={"city": "Dallas"}).status_code == 401


# ---- Delete ---------------------------------------------------------------------------------

def test_delete_soft_deletes(client: TestClient, create_patient) -> None:
    patient = create_patient()
    url = f"/patients/{patient['patient_id']}"
    response = client.delete(url, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["data"]["deleted_at"] is not None

    assert client.get(url).status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.delete(url, headers=AUTH).status_code == 404


def test_delete_requires_api_key(client: TestClient, create_patient) -> None:
    patient = create_patient()
    assert client.delete(f"/patients/{patient['patient_id']}").status_code == 401


# ---- Errors and misc ------------------------------------------------------------------------

def test_unknown_route_uses_envelope(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert_envelope(response.json())


def test_database_failure_returns_503_envelope(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise OperationalError("INSERT", {}, Exception("connection refused"))

    monkeypatch.setattr(patient_service, "create_patient", broken)
    response = client.post("/patients", json=VALID_PATIENT, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "connection refused" not in response.text


def test_swagger_documents_api_key_header(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "X-API-Key" in {s["name"] for s in schema["components"]["securitySchemes"].values()}


def test_seed_inserts_demo_patients_once(client: TestClient) -> None:
    with SessionLocal() as db:
        assert seed_demo_patients(db) == len(DEMO_PATIENTS)
        assert seed_demo_patients(db) == 0
    assert len(client.get("/patients").json()["data"]) == len(DEMO_PATIENTS)
