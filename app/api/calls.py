"""Read-only endpoints for Vapi call logs (transcripts, outcomes, collected data)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.patients import parse_patient_id
from app.db import get_db
from app.schemas import CallLogOut, Envelope
from app.services import calls as call_service
from app.services import patients as patient_service

router = APIRouter(tags=["calls"])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/calls", response_model=Envelope[list[CallLogOut]], summary="List calls")
def list_calls(db: DbSession, limit: Limit = 50, offset: Offset = 0) -> Envelope[list[CallLogOut]]:
    """All calls, newest first, including incomplete ones (caller hung up before saving)."""
    calls = call_service.list_calls(db, limit=limit, offset=offset)
    return Envelope[list[CallLogOut]](data=[CallLogOut.model_validate(c) for c in calls])


@router.get(
    "/patients/{patient_id}/calls", response_model=Envelope[list[CallLogOut]], summary="List a patient's calls"
)
def list_patient_calls(
    patient_id: str, db: DbSession, limit: Limit = 50, offset: Offset = 0
) -> Envelope[list[CallLogOut]]:
    """Calls that created or updated this patient, newest first."""
    patient = patient_service.get_patient(db, parse_patient_id(patient_id))
    calls = call_service.list_calls(db, patient_id=patient.patient_id, limit=limit, offset=offset)
    return Envelope[list[CallLogOut]](data=[CallLogOut.model_validate(c) for c in calls])
