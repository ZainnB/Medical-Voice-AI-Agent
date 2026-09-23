import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.services import patients as patient_service
from tests.conftest import AUTH, VALID_PATIENT

VAPI_AUTH = {"X-Vapi-Secret": "test-vapi-secret"}
CALLER = "+12125550123"


def tool_message(*tool_calls: dict, call_id: str = "call-1") -> dict:
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": CALLER}},
            "toolCallList": list(tool_calls),
        }
    }


def flat(name: str, arguments: dict, call_id: str = "tc-1") -> dict:
    """The flat shape from Vapi's server-events docs."""
    return {"id": call_id, "name": name, "arguments": arguments}


def call_tools(client: TestClient, payload: dict) -> list[dict]:
    """POST a tool-calls payload; assert Vapi's contract and return the parsed results."""
    response = client.post("/vapi/tools", json=payload, headers=VAPI_AUTH)
    assert response.status_code == 200
    results = response.json()["results"]
    for item in results:
        assert isinstance(item["result"], str) and "\n" not in item["result"]
    return [json.loads(item["result"]) | {"_toolCallId": item["toolCallId"]} for item in results]


def run(client: TestClient, name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return call_tools(client, tool_message(flat(name, arguments), call_id=call_id))[0]


def end_of_call(client: TestClient, call_id: str = "call-1", **extra: object) -> None:
    message = {
        "type": "end-of-call-report",
        "endedReason": "customer-ended-call",
        "call": {"id": call_id, "customer": {"number": CALLER}},
        "artifact": {"transcript": "AI: Hi, I'm Riley.\nUser: Hi, I'm Jane."},
        "analysis": {"summary": "Caller registered as a new patient."},
        "startedAt": "2026-09-23T18:00:00.000Z",
        "endedAt": "2026-09-23T18:05:00.000Z",
    } | extra
    response = client.post("/vapi/events", json={"message": message}, headers=VAPI_AUTH)
    assert response.status_code == 200


def patients(client: TestClient) -> list[dict]:
    return client.get("/patients").json()["data"]


def calls(client: TestClient) -> list[dict]:
    return client.get("/calls").json()["data"]


# ---- Authentication ------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [{}, {"X-Vapi-Secret": "wrong"}, {"Authorization": "Bearer wrong"}])
@pytest.mark.parametrize("path", ["/vapi/tools", "/vapi/events"])
def test_webhooks_reject_missing_or_wrong_secret(client: TestClient, path: str, headers: dict) -> None:
    response = client.post(path, json=tool_message(flat("validate_patient", {})), headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers", [{"X-Vapi-Secret": "test-vapi-secret"}, {"Authorization": "Bearer test-vapi-secret"}]
)
def test_webhooks_accept_either_secret_header(client: TestClient, headers: dict) -> None:
    response = client.post("/vapi/tools", json=tool_message(flat("validate_patient", {})), headers=headers)
    assert response.status_code == 200


# ---- Payload shapes ------------------------------------------------------------------------

def test_create_patient_via_tool(client: TestClient) -> None:
    result = run(client, "create_patient", VALID_PATIENT)
    assert result["ok"] is True
    assert result["first_name"] == "Jane"
    [patient] = patients(client)
    assert patient["patient_id"] == result["patient_id"]
    [call] = calls(client)
    assert call["outcome"] == "created"
    assert call["patient_id"] == result["patient_id"]
    assert call["caller_number"] == CALLER
    assert call["collected_data"]["last_name"] == "Doe"


def test_response_echoes_tool_call_id_and_name(client: TestClient) -> None:
    response = client.post("/vapi/tools", json=tool_message(flat("validate_patient", {}, "abc")), headers=VAPI_AUTH)
    [item] = response.json()["results"]
    assert item["toolCallId"] == "abc"
    assert item["name"] == "validate_patient"


def test_openai_shape_with_string_arguments(client: TestClient) -> None:
    tool_call = {
        "id": "tc-9",
        "type": "function",
        "function": {"name": "create_patient", "arguments": json.dumps(VALID_PATIENT)},
    }
    [result] = call_tools(client, tool_message(tool_call))
    assert result["ok"] is True
    assert result["_toolCallId"] == "tc-9"


def test_flat_shape_with_parameters_key(client: TestClient) -> None:
    tool_call = {"id": "tc-2", "name": "create_patient", "parameters": VALID_PATIENT}
    [result] = call_tools(client, tool_message(tool_call))
    assert result["ok"] is True


def test_tool_with_tool_call_list_only(client: TestClient) -> None:
    payload = tool_message()
    payload["message"]["toolWithToolCallList"] = [
        {
            "type": "function",
            "name": "create_patient",
            "toolCall": {"id": "tc-3", "type": "function",
                         "function": {"name": "create_patient", "parameters": VALID_PATIENT}},
        }
    ]
    [result] = call_tools(client, payload)
    assert result["ok"] is True
    assert result["_toolCallId"] == "tc-3"


def test_multiple_tool_calls_in_one_request(client: TestClient) -> None:
    results = call_tools(
        client,
        tool_message(
            flat("validate_patient", VALID_PATIENT, "a"),
            flat("lookup_patient_by_phone", {"phone_number": "2125550123"}, "b"),
        ),
    )
    assert [r["_toolCallId"] for r in results] == ["a", "b"]
    assert results[0]["ok"] is True
    assert results[1] == {"found": False, "_toolCallId": "b"}


def test_malformed_argument_string_returns_readable_error(client: TestClient) -> None:
    tool_call = {"id": "tc-4", "function": {"name": "create_patient", "arguments": "{not json"}}
    [result] = call_tools(client, tool_message(tool_call))
    assert result["ok"] is False
    assert patients(client) == []


def test_unknown_tool_returns_readable_error(client: TestClient) -> None:
    result = run(client, "delete_everything", {})
    assert result["ok"] is False
    assert "Unknown tool" in result["message"]


def test_non_tool_events_are_acknowledged(client: TestClient) -> None:
    response = client.post("/vapi/events", json={"message": {"type": "status-update"}}, headers=VAPI_AUTH)
    assert response.status_code == 200
    assert response.json() == {}


def test_invalid_json_body_is_acknowledged(client: TestClient) -> None:
    response = client.post(
        "/vapi/tools", content=b"{oops", headers=VAPI_AUTH | {"Content-Type": "application/json"}
    )
    assert response.status_code == 200


# ---- Validation and resilience -------------------------------------------------------------

def test_invalid_dob_returns_200_with_field_error(client: TestClient) -> None:
    result = run(client, "create_patient", VALID_PATIENT | {"date_of_birth": "01/01/2999"})
    assert result["ok"] is False
    assert result["errors"] == [{"field": "date_of_birth", "message": "Date of birth can't be in the future"}]
    assert patients(client) == []


def test_validate_patient_normalizes_without_saving(client: TestClient) -> None:
    result = run(client, "validate_patient", VALID_PATIENT | {"state": "Texas", "sex": "f"})
    assert result["ok"] is True
    assert result["normalized"]["state"] == "TX"
    assert result["normalized"]["sex"] == "Female"
    assert result["normalized"]["date_of_birth"] == "03/04/1990"
    assert patients(client) == []


def test_validate_patient_reports_missing_required_fields(client: TestClient) -> None:
    result = run(client, "validate_patient", {"first_name": "Jane"})
    assert result["ok"] is False
    assert {e["field"] for e in result["errors"]} >= {"last_name", "date_of_birth", "zip_code"}


def test_create_is_idempotent_per_call(client: TestClient) -> None:
    first = run(client, "create_patient", VALID_PATIENT)
    second = run(client, "create_patient", VALID_PATIENT | {"last_name": "Davis"})
    assert first["patient_id"] == second["patient_id"]
    [patient] = patients(client)
    assert patient["last_name"] == "Davis"


def test_separate_calls_create_separate_patients(client: TestClient) -> None:
    run(client, "create_patient", VALID_PATIENT, call_id="call-1")
    run(client, "create_patient", VALID_PATIENT | {"first_name": "Jim"}, call_id="call-2")
    assert len(patients(client)) == 2


def test_database_failure_returns_200_with_system_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> None:
        raise OperationalError("INSERT", {}, Exception("connection refused"))

    monkeypatch.setattr(patient_service, "create_patient", broken)
    result = run(client, "create_patient", VALID_PATIENT)
    assert result["ok"] is False
    assert result["system_error"] is True
    assert patients(client) == []
    [call] = calls(client)
    assert call["outcome"] == "failed"
    assert call["collected_data"]["first_name"] == "Jane"


# ---- Returning callers ---------------------------------------------------------------------

@pytest.fixture()
def existing(create_patient) -> dict:
    return create_patient()


def test_lookup_returns_names_but_never_dob(client: TestClient, existing: dict) -> None:
    result = run(client, "lookup_patient_by_phone", {"phone_number": "+1 (212) 555-0123"})
    assert result["found"] is True
    assert result["patients"] == [
        {"patient_id": existing["patient_id"], "first_name": "Jane", "last_name": "Doe"}
    ]
    assert "03/04/1990" not in json.dumps(result)


def test_lookup_invalid_phone(client: TestClient) -> None:
    result = run(client, "lookup_patient_by_phone", {"phone_number": "555"})
    assert result["found"] is False
    assert result["errors"][0]["field"] == "phone_number"


@pytest.mark.parametrize(("dob", "verified"), [("03/04/1990", True), ("1990-03-04", True), ("03/05/1990", False)])
def test_verify_identity(client: TestClient, existing: dict, dob: str, verified: bool) -> None:
    result = run(client, "verify_patient_identity", {"patient_id": existing["patient_id"], "date_of_birth": dob})
    assert result["verified"] is verified
    assert "03/04/1990" not in json.dumps(result)


def test_verify_identity_unknown_patient(client: TestClient) -> None:
    result = run(client, "verify_patient_identity", {"patient_id": str(uuid.uuid4()), "date_of_birth": "01/01/1990"})
    assert result["ok"] is False


def test_update_requires_matching_dob(client: TestClient, existing: dict) -> None:
    result = run(
        client,
        "update_patient",
        {"patient_id": existing["patient_id"], "verified_date_of_birth": "01/01/1980", "city": "Dallas"},
    )
    assert result == {"ok": False, "verified": False, "message": "The date of birth does not match our records",
                      "_toolCallId": "tc-1"}
    assert patients(client)[0]["city"] == "Austin"


def test_update_changes_only_given_fields(client: TestClient, existing: dict) -> None:
    result = run(
        client,
        "update_patient",
        {"patient_id": existing["patient_id"], "verified_date_of_birth": "03/04/1990",
         "address_line_1": "500 Oak Ave", "zip_code": "75201"},
    )
    assert result["ok"] is True
    [patient] = patients(client)
    assert (patient["address_line_1"], patient["zip_code"], patient["city"]) == ("500 Oak Ave", "75201", "Austin")
    [call] = calls(client)
    assert call["outcome"] == "updated"
    patient_calls = client.get(f"/patients/{existing['patient_id']}/calls").json()["data"]
    assert [c["vapi_call_id"] for c in patient_calls] == ["call-1"]


def test_update_can_correct_date_of_birth(client: TestClient, existing: dict) -> None:
    result = run(
        client,
        "update_patient",
        {"patient_id": existing["patient_id"], "verified_date_of_birth": "03/04/1990", "date_of_birth": "03/14/1990"},
    )
    assert result["ok"] is True
    assert patients(client)[0]["date_of_birth"] == "03/14/1990"


def test_update_rejects_invalid_change(client: TestClient, existing: dict) -> None:
    result = run(
        client,
        "update_patient",
        {"patient_id": existing["patient_id"], "verified_date_of_birth": "03/04/1990", "zip_code": "1"},
    )
    assert result["ok"] is False
    assert result["errors"][0]["field"] == "zip_code"


# ---- End-of-call report --------------------------------------------------------------------

def test_end_of_call_report_for_completed_registration(client: TestClient) -> None:
    run(client, "create_patient", VALID_PATIENT)
    end_of_call(client)
    [call] = calls(client)
    assert call["outcome"] == "created"
    assert call["transcript"].startswith("AI: Hi, I'm Riley.")
    assert call["summary"] == "Caller registered as a new patient."
    assert call["ended_reason"] == "customer-ended-call"
    assert call["started_at"] == "2026-09-23T18:00:00+00:00"
    assert call["ended_at"] == "2026-09-23T18:05:00+00:00"


def test_dropped_call_is_incomplete_with_collected_data(client: TestClient) -> None:
    run(client, "validate_patient", {"first_name": "Jane", "last_name": "Doe"})
    end_of_call(client, endedReason="customer-ended-call")
    [call] = calls(client)
    assert call["outcome"] == "incomplete"
    assert call["patient_id"] is None
    assert call["collected_data"] == {"first_name": "Jane", "last_name": "Doe"}
    assert patients(client) == []


def test_end_of_call_before_any_tool_call(client: TestClient) -> None:
    end_of_call(client, call_id="call-early")
    run(client, "create_patient", VALID_PATIENT, call_id="call-early")
    [call] = calls(client)
    assert call["outcome"] == "created"
    assert call["transcript"] is not None


def test_transcript_rebuilt_from_messages(client: TestClient) -> None:
    end_of_call(
        client,
        artifact={"messages": [
            {"role": "system", "message": "You are Riley"},
            {"role": "bot", "message": "Hi, I'm Riley."},
            {"role": "user", "message": "Hello."},
        ]},
    )
    assert calls(client)[0]["transcript"] == "AI: Hi, I'm Riley.\nUser: Hello."


def test_patient_calls_unknown_patient_is_404(client: TestClient) -> None:
    assert client.get(f"/patients/{uuid.uuid4()}/calls").status_code == 404


def test_calls_endpoint_requires_no_auth_but_writes_do(client: TestClient) -> None:
    assert client.get("/calls").status_code == 200
    assert client.post("/patients", json=VALID_PATIENT).status_code == 401
    assert client.post("/patients", json=VALID_PATIENT, headers=AUTH).status_code == 201
