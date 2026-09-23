from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.services import appointments as appointment_service
from tests.conftest import VALID_PATIENT
from tests.test_vapi_webhook import run


def register(client: TestClient, call_id: str = "call-1", **overrides: str) -> str:
    result = run(client, "create_patient", VALID_PATIENT | overrides, call_id=call_id)
    assert result["ok"] is True
    return result["patient_id"]


def offered(client: TestClient, call_id: str = "call-1", **filters: str) -> list[dict]:
    return run(client, "get_available_slots", filters, call_id=call_id)["slots"]


# ---- Mock calendar --------------------------------------------------------------------------

def test_schedule_is_weekdays_only_and_in_the_future() -> None:
    now = datetime(2026, 9, 23, 23, 0, tzinfo=UTC)  # a Wednesday evening
    slots = appointment_service.schedule(now)
    zone = appointment_service.clinic_zone()
    assert slots and all(s.astimezone(zone).weekday() < 5 and s > now for s in slots)
    assert appointment_service.spoken(slots[0]) == "Thursday, September 24 at 9 AM"
    assert appointment_service.spoken(slots[1]) == "Thursday, September 24 at 10:30 AM"
    assert appointment_service.provider_for(slots[0]) == appointment_service.MORNING_PROVIDER
    assert appointment_service.provider_for(slots[2]) == appointment_service.AFTERNOON_PROVIDER


# ---- Tools ----------------------------------------------------------------------------------

def test_get_available_slots_offers_three_spoken_options(client: TestClient) -> None:
    slots = offered(client)
    assert len(slots) == 3
    assert {"slot_id", "spoken", "provider"} <= set(slots[0])


def test_get_available_slots_filters_by_date_and_part_of_day(client: TestClient) -> None:
    first = datetime.fromisoformat(offered(client)[0]["slot_id"])
    local_day = first.astimezone(appointment_service.clinic_zone()).strftime("%m/%d/%Y")
    afternoon = offered(client, preferred_date=local_day, part_of_day="afternoon")
    assert afternoon and all("PM" in s["spoken"] for s in afternoon)


def test_get_available_slots_falls_back_when_nothing_matches(client: TestClient) -> None:
    result = run(client, "get_available_slots", {"preferred_date": "01/01/2020"})
    assert result["slots"] and "note" in result


def test_book_after_registration(client: TestClient) -> None:
    patient_id = register(client)
    slot = offered(client)[0]
    result = run(client, "book_appointment", {"patient_id": patient_id, "slot_id": slot["slot_id"],
                                              "reason": "annual checkup"})
    assert result["ok"] is True
    assert result["spoken"] == slot["spoken"]
    [appointment] = client.get(f"/patients/{patient_id}/appointments").json()["data"]
    assert appointment["provider"] == slot["provider"]
    assert appointment["reason"] == "annual checkup"
    assert appointment["vapi_call_id"] == "call-1"
    assert slot["slot_id"] not in {s["slot_id"] for s in offered(client)}
    assert len(client.get("/appointments").json()["data"]) == 1


def test_booking_is_idempotent_and_one_per_patient(client: TestClient) -> None:
    patient_id = register(client)
    first, second = offered(client)[:2]
    booked = run(client, "book_appointment", {"patient_id": patient_id, "slot_id": first["slot_id"]})
    again = run(client, "book_appointment", {"patient_id": patient_id, "slot_id": first["slot_id"]})
    assert again["ok"] is True and again["appointment_id"] == booked["appointment_id"]
    other = run(client, "book_appointment", {"patient_id": patient_id, "slot_id": second["slot_id"]})
    assert other["ok"] is False and other["already_booked"] is True


def test_cannot_book_for_a_patient_not_saved_on_this_call(client: TestClient) -> None:
    someone_else = register(client, call_id="call-1")
    slot = offered(client, call_id="call-2")[0]
    result = run(client, "book_appointment", {"patient_id": someone_else, "slot_id": slot["slot_id"]}, call_id="call-2")
    assert result["ok"] is False
    assert client.get("/appointments").json()["data"] == []


@pytest.mark.parametrize("slot_id", ["2026-09-26T09:00:00-04:00", "not-a-time", "2020-01-02T09:00:00-05:00"])
def test_rejects_times_not_on_the_schedule(client: TestClient, slot_id: str) -> None:
    patient_id = register(client)
    result = run(client, "book_appointment", {"patient_id": patient_id, "slot_id": slot_id})
    assert result["ok"] is False
    assert result["errors"][0]["field"] == "slot_id"


def test_slot_taken_by_another_caller(client: TestClient) -> None:
    first = register(client, call_id="call-1")
    second = register(client, call_id="call-2", first_name="Jim", date_of_birth="01/02/1980")
    slot = offered(client)[0]
    assert run(client, "book_appointment", {"patient_id": first, "slot_id": slot["slot_id"]})["ok"] is True
    result = run(client, "book_appointment", {"patient_id": second, "slot_id": slot["slot_id"]}, call_id="call-2")
    assert result["ok"] is False and result["slot_taken"] is True


def test_database_index_blocks_double_booking_race(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    first = register(client, call_id="call-1")
    second = register(client, call_id="call-2", first_name="Jim", date_of_birth="01/02/1980")
    slot = offered(client)[0]
    run(client, "book_appointment", {"patient_id": first, "slot_id": slot["slot_id"]})
    monkeypatch.setattr(appointment_service, "is_free", lambda db, starts_at: True)  # simulate losing the race
    result = run(client, "book_appointment", {"patient_id": second, "slot_id": slot["slot_id"]}, call_id="call-2")
    assert result["slot_taken"] is True
    assert len(client.get("/appointments").json()["data"]) == 1
