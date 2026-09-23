"""Call-log business logic: one row per Vapi call, upserted by the Vapi call ID.

Tool calls and the end-of-call report can arrive in either order, so every writer goes
through get_or_create_call_log().
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CallLog


def get_or_create_call_log(db: Session, vapi_call_id: str, caller_number: str | None = None) -> CallLog:
    """Return the call log for this Vapi call, creating it if needed (safe under concurrent webhooks).

    Call this first in a unit of work: on an insert race it rolls back the session.
    """
    query = select(CallLog).where(CallLog.vapi_call_id == vapi_call_id)
    call_log = db.scalar(query)
    if call_log is None:
        call_log = CallLog(vapi_call_id=vapi_call_id, caller_number=caller_number)
        db.add(call_log)
        try:
            db.flush()
        except IntegrityError:
            # Another webhook for the same call inserted it first; use that row.
            db.rollback()
            call_log = db.scalars(query).one()
    if caller_number and not call_log.caller_number:
        call_log.caller_number = caller_number
    return call_log


def record_collected_data(call_log: CallLog, arguments: dict[str, Any]) -> None:
    """Merge tool arguments into collected_data so a dropped call still shows what was gathered."""
    merged = dict(call_log.collected_data or {})
    merged.update({key: value for key, value in arguments.items() if value not in (None, "")})
    call_log.collected_data = merged  # reassign (not mutate) so SQLAlchemy sees the change


def link_patient(call_log: CallLog, patient_id: uuid.UUID, outcome: str) -> None:
    """Attach the saved patient to the call. A call that created a patient stays 'created'."""
    call_log.patient_id = patient_id
    if call_log.outcome != "created":
        call_log.outcome = outcome


def record_end_of_call(
    db: Session,
    *,
    vapi_call_id: str,
    caller_number: str | None,
    ended_reason: str | None,
    summary: str | None,
    transcript: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
) -> CallLog:
    """Store the end-of-call report. Outcome stays as set by the tools ('incomplete' if nothing was saved)."""
    call_log = get_or_create_call_log(db, vapi_call_id, caller_number)
    call_log.ended_reason = ended_reason
    call_log.summary = summary
    call_log.transcript = transcript
    call_log.started_at = started_at
    call_log.ended_at = ended_at
    db.commit()
    db.refresh(call_log)
    return call_log


def list_calls(
    db: Session, *, patient_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0
) -> list[CallLog]:
    """List call logs, newest first, optionally for one patient."""
    query = select(CallLog)
    if patient_id is not None:
        query = query.where(CallLog.patient_id == patient_id)
    query = query.order_by(CallLog.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(query))
