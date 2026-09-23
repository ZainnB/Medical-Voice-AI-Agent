"""Read-only endpoints for appointments booked by the voice agent."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.patients import parse_patient_id
from app.db import get_db
from app.schemas import AppointmentOut, Envelope
from app.services import appointments as appointment_service
from app.services import patients as patient_service

router = APIRouter(tags=["appointments"])

DbSession = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/appointments", response_model=Envelope[list[AppointmentOut]], summary="List appointments")
def list_appointments(db: DbSession, limit: Limit = 50, offset: Offset = 0) -> Envelope[list[AppointmentOut]]:
    """Booked appointments, soonest first."""
    appointments = appointment_service.list_appointments(db, limit=limit, offset=offset)
    return Envelope[list[AppointmentOut]](data=[AppointmentOut.model_validate(a) for a in appointments])


@router.get(
    "/patients/{patient_id}/appointments",
    response_model=Envelope[list[AppointmentOut]],
    summary="List a patient's appointments",
)
def list_patient_appointments(patient_id: str, db: DbSession) -> Envelope[list[AppointmentOut]]:
    """Booked appointments for one patient, soonest first."""
    patient = patient_service.get_patient(db, parse_patient_id(patient_id))
    appointments = appointment_service.list_appointments(db, patient_id=patient.patient_id)
    return Envelope[list[AppointmentOut]](data=[AppointmentOut.model_validate(a) for a in appointments])
