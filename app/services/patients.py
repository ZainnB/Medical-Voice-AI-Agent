"""Patient business logic, shared by the REST API and the Vapi tool webhook."""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models import Patient, utc_now
from app.schemas import PatientCreate, PatientUpdate


def create_patient(db: Session, data: PatientCreate) -> Patient:
    """Insert a new patient from already-validated data."""
    patient = Patient(**data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    """Return an active (not deleted) patient, or raise NotFoundError."""
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise NotFoundError(f"Patient {patient_id} not found")
    return patient


def list_patients(
    db: Session,
    *,
    last_name: str | None = None,
    date_of_birth: date | None = None,
    phone_number: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Patient]:
    """List active patients, newest first, optionally filtered (filters are pre-normalized)."""
    query = select(Patient).where(Patient.deleted_at.is_(None))
    if last_name:
        query = query.where(func.lower(Patient.last_name) == last_name.lower())
    if date_of_birth:
        query = query.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        query = query.where(Patient.phone_number == phone_number)
    query = query.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(query))


def update_patient(db: Session, patient_id: uuid.UUID, data: PatientUpdate) -> Patient:
    """Apply a partial update: only fields present in the request change."""
    patient = get_patient(db, patient_id)
    for field, value in data.changes().items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    return patient


def delete_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    """Soft-delete a patient by stamping deleted_at."""
    patient = get_patient(db, patient_id)
    patient.deleted_at = utc_now()
    db.commit()
    db.refresh(patient)
    return patient
