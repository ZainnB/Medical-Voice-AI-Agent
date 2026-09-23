"""The functions the voice agent can call during a phone call (Vapi function tools).

Each tool returns a small dict that is sent back to the LLM as compact JSON. Tools never
raise: validation problems come back as {"ok": false, "errors": [...]}, and database or
unexpected failures as {"ok": false, "system_error": true, ...}, so the agent can always
tell the caller the truth about whether their information was saved.

Identity rule for returning callers: the LLM never sees a stored date of birth. Lookup
returns only IDs and names; the server compares the DOB the caller says (verify_patient_identity,
and again inside update_patient).
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app import validators
from app.errors import NotFoundError
from app.models import CallLog, Patient
from app.schemas import PatientCreate, PatientUpdate, validation_error_details
from app.services import appointments as appointment_service
from app.services import calls as call_service
from app.services import patients as patient_service

logger = logging.getLogger(__name__)

ToolResult = dict[str, Any]
SYSTEM_ERROR: ToolResult = {
    "ok": False,
    "system_error": True,
    "message": "The registration system is temporarily unavailable. Nothing was saved.",
}
MAX_LOOKUP_MATCHES = 5
# Stops a caller from guessing birth dates against someone else's record.
MAX_VERIFICATION_FAILURES = 3
LOCKED: ToolResult = {
    "ok": False,
    "verified": False,
    "locked": True,
    "message": "Too many incorrect attempts. Do not try again on this call; offer to register as a new patient.",
}


@dataclass(frozen=True)
class CallContext:
    """Who is calling, as reported by Vapi. call_id is None for tool tests outside a call."""

    call_id: str | None
    caller_number: str | None


def _errors(exc: ValidationError) -> ToolResult:
    return {"ok": False, "errors": [d.model_dump() for d in validation_error_details(exc.errors())]}


def _field_error(field: str, message: str) -> ToolResult:
    return {"ok": False, "errors": [{"field": field, "message": message}]}


def _check_identity(
    db: Session, raw_patient_id: Any, raw_dob: Any, call_log: CallLog | None
) -> Patient | ToolResult:
    """Return the patient if the spoken DOB matches the record, otherwise a result explaining why not.

    After MAX_VERIFICATION_FAILURES wrong dates in one call, every further check is refused.
    """
    if call_log is not None and call_service.verification_failures(call_log) >= MAX_VERIFICATION_FAILURES:
        return LOCKED
    try:
        patient_id = uuid.UUID(str(raw_patient_id))
    except ValueError:
        return _field_error("patient_id", "Use the patient_id returned by lookup_patient_by_phone")
    try:
        spoken_dob = validators.parse_date(raw_dob)
    except ValueError as exc:
        return _field_error("date_of_birth", str(exc))
    try:
        patient = patient_service.get_patient(db, patient_id)
    except NotFoundError:
        return _field_error("patient_id", "No patient found with that ID")
    if patient.date_of_birth != spoken_dob:
        if call_log is not None:
            call_service.record_verification_failure(call_log)
        return {"ok": False, "verified": False, "message": "The date of birth does not match our records"}
    return patient


# ---- Tools -----------------------------------------------------------------------------------

def lookup_patient_by_phone(db: Session, args: dict[str, Any], _: CallLog | None) -> ToolResult:
    """Find active patients by phone. Returns names only, never dates of birth or other details."""
    try:
        phone = validators.normalize_phone(args.get("phone_number"))
    except ValueError as exc:
        return {"found": False, **_field_error("phone_number", str(exc))}
    patients = patient_service.list_patients(db, phone_number=phone, limit=MAX_LOOKUP_MATCHES)
    if not patients:
        return {"found": False}
    return {
        "found": True,
        "patients": [
            {"patient_id": str(p.patient_id), "first_name": p.first_name, "last_name": p.last_name}
            for p in patients
        ],
    }


def verify_patient_identity(db: Session, args: dict[str, Any], call_log: CallLog | None) -> ToolResult:
    """Check the date of birth the caller gave against the record (server-side comparison)."""
    result = _check_identity(db, args.get("patient_id"), args.get("date_of_birth"), call_log)
    if isinstance(result, Patient):
        return {"ok": True, "verified": True, "patient_id": str(result.patient_id), "first_name": result.first_name}
    return result


def validate_patient(db: Session, args: dict[str, Any], _: CallLog | None) -> ToolResult:
    """Run full validation without saving; returns normalized values or per-field errors."""
    try:
        data = PatientCreate.model_validate(args)
    except ValidationError as exc:
        return _errors(exc)
    return {"ok": True, "normalized": data.model_dump(mode="json")}


def create_patient(db: Session, args: dict[str, Any], call_log: CallLog | None) -> ToolResult:
    """Validate and save a new patient. Idempotent per call: a retry updates the same record."""
    try:
        data = PatientCreate.model_validate(args)
    except ValidationError as exc:
        return _errors(exc)

    patient: Patient | None = None
    if call_log is not None and call_log.patient_id and call_log.outcome == "created":
        try:
            patient = patient_service.update_patient(db, call_log.patient_id, data.model_dump(), commit=False)
        except NotFoundError:
            patient = None  # deleted since; fall through to a fresh insert
    if patient is None:
        duplicate = patient_service.find_exact_duplicate(db, data)
        if duplicate is not None:
            # Safety net if the agent skipped the lookup: never register the same person twice.
            # The caller just said this exact name, DOB, and phone, so confirming it reveals nothing new.
            return {
                "ok": False,
                "already_registered": True,
                "patient_id": str(duplicate.patient_id),
                "first_name": duplicate.first_name,
                "message": "This patient is already registered. Offer to update their information instead.",
            }
        patient = patient_service.create_patient(db, data, commit=False)
    if call_log is not None:
        call_service.link_patient(call_log, patient.patient_id, "created")
    return {"ok": True, "patient_id": str(patient.patient_id), "first_name": patient.first_name}


def update_patient(db: Session, args: dict[str, Any], call_log: CallLog | None) -> ToolResult:
    """Update a returning patient after re-checking identity; only the given fields change."""
    changes = dict(args)
    identity = _check_identity(
        db, changes.pop("patient_id", None), changes.pop("verified_date_of_birth", None), call_log
    )
    if not isinstance(identity, Patient):
        return identity
    try:
        update = PatientUpdate.model_validate(changes)
    except ValidationError as exc:
        return _errors(exc)
    patient = patient_service.update_patient(db, identity.patient_id, update.changes(), commit=False)
    if call_log is not None:
        call_service.link_patient(call_log, patient.patient_id, "updated")
    return {"ok": True, "patient_id": str(patient.patient_id), "first_name": patient.first_name}


def get_available_slots(db: Session, args: dict[str, Any], _: CallLog | None) -> ToolResult:
    """Offer up to three open first-appointment times, optionally on a date and/or morning/afternoon."""
    on = None
    if args.get("preferred_date"):
        try:
            on = validators.parse_date(args["preferred_date"])
        except ValueError:
            return _field_error("preferred_date", "Preferred date must be in MM/DD/YYYY format")
    part_of_day = args.get("part_of_day") if args.get("part_of_day") in ("morning", "afternoon") else None
    slots = appointment_service.available_slots(db, on=on, part_of_day=part_of_day)
    note = None
    if not slots and (on or part_of_day):
        slots = appointment_service.available_slots(db)
        note = "Nothing is open at that time; these are the next available openings."
    return {
        "slots": [
            {"slot_id": slot.isoformat(), "spoken": appointment_service.spoken(slot),
             "provider": appointment_service.provider_for(slot)}
            for slot in slots
        ],
        **({"note": note} if note else {}),
    }


def book_appointment(db: Session, args: dict[str, Any], call_log: CallLog | None) -> ToolResult:
    """Book a slot for the patient saved on this call. Idempotent for the same slot."""
    try:
        patient_id = uuid.UUID(str(args.get("patient_id")))
    except ValueError:
        return _field_error("patient_id", "Use the patient_id returned by create_patient")
    if call_log is not None and call_log.patient_id != patient_id:
        return {"ok": False, "message": "Appointments can only be booked for the patient saved on this call."}
    try:
        patient_service.get_patient(db, patient_id)
    except NotFoundError:
        return _field_error("patient_id", "No patient found with that ID")
    try:
        starts_at = datetime.fromisoformat(str(args.get("slot_id")))
    except ValueError:
        starts_at = None
    if starts_at is not None and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=appointment_service.clinic_zone())
    if starts_at is None or not appointment_service.is_offered_slot(starts_at):
        return _field_error("slot_id", "That time isn't on the schedule; offer one of the options from get_available_slots")
    try:
        reason = validators.optional(lambda v: validators.normalize_text(v, "Reason", 200))(args.get("reason"))
    except ValueError as exc:
        return _field_error("reason", str(exc))

    existing = appointment_service.list_appointments(db, patient_id=patient_id, upcoming_only=True, limit=1)
    if existing:
        same = appointment_service.as_utc(existing[0].starts_at) == appointment_service.as_utc(starts_at)
        booked = {"spoken": appointment_service.spoken(appointment_service.as_utc(existing[0].starts_at)),
                  "provider": existing[0].provider}
        return {"ok": True, "appointment_id": str(existing[0].id), **booked} if same else {
            "ok": False, "already_booked": True, **booked,
            "message": "This patient already has an upcoming appointment.",
        }
    taken = {"ok": False, "slot_taken": True, "message": "That time was just taken. Offer another option."}
    if not appointment_service.is_free(db, starts_at):
        return taken
    try:
        appointment = appointment_service.book(
            db, patient_id=patient_id, starts_at=starts_at, reason=reason,
            vapi_call_id=call_log.vapi_call_id if call_log else None,
        )
    except IntegrityError:
        db.rollback()  # another caller booked it between our check and the insert
        return taken
    return {"ok": True, "appointment_id": str(appointment.id), "spoken": appointment_service.spoken(starts_at),
            "provider": appointment.provider}


TOOLS: dict[str, Callable[[Session, dict[str, Any], CallLog | None], ToolResult]] = {
    "lookup_patient_by_phone": lookup_patient_by_phone,
    "verify_patient_identity": verify_patient_identity,
    "validate_patient": validate_patient,
    "create_patient": create_patient,
    "update_patient": update_patient,
    "get_available_slots": get_available_slots,
    "book_appointment": book_appointment,
}
SAVING_TOOLS = {"create_patient", "update_patient"}


def run_tool(db: Session, name: str, args: dict[str, Any], context: CallContext) -> ToolResult:
    """Run one tool in its own transaction and always return a result (never raise)."""
    handler = TOOLS.get(name)
    if handler is None:
        return {"ok": False, "message": f"Unknown tool: {name}"}
    try:
        call_log = None
        if context.call_id:
            call_log = call_service.get_or_create_call_log(db, context.call_id, context.caller_number)
            call_service.record_collected_data(call_log, args)
        result = handler(db, args, call_log)
        db.commit()
        return result
    except Exception:
        db.rollback()
        logger.exception("tool_failed", extra={"fields": {"tool": name, "call_id": context.call_id}})
        if name in SAVING_TOOLS:
            _mark_save_failed(db, context, args)
        return SYSTEM_ERROR


def _mark_save_failed(db: Session, context: CallContext, args: dict[str, Any]) -> None:
    """Best effort: flag the call as 'failed' (keeping what was collected) for the dashboard."""
    if not context.call_id:
        return
    try:
        call_log = call_service.get_or_create_call_log(db, context.call_id, context.caller_number)
        call_service.record_collected_data(call_log, args)
        if call_log.outcome == "incomplete":
            call_log.outcome = "failed"
        db.commit()
    except SQLAlchemyError:
        db.rollback()
