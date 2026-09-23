"""REST endpoints for patients. HTTP concerns only; business logic lives in app.services."""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app import validators
from app.db import get_db
from app.errors import BadRequestError
from app.schemas import Envelope, ErrorDetail, PatientCreate, PatientOut, PatientUpdate
from app.security import require_api_key
from app.services import patients as service

router = APIRouter(prefix="/patients", tags=["patients"])

T = TypeVar("T")
DbSession = Annotated[Session, Depends(get_db)]


def parse_patient_id(patient_id: str) -> uuid.UUID:
    """Parse a path UUID, returning 400 (not FastAPI's default 422) when malformed."""
    try:
        return uuid.UUID(patient_id)
    except ValueError:
        raise BadRequestError(
            "patient_id must be a valid UUID",
            [ErrorDetail(field="patient_id", message="patient_id must be a valid UUID")],
        ) from None


def _parse_filter(value: str | None, parse: Callable[[str], T], field: str) -> T | None:
    if value is None or not value.strip():
        return None
    try:
        return parse(value)
    except ValueError as exc:
        raise BadRequestError("Invalid query parameter", [ErrorDetail(field=field, message=str(exc))]) from None


def _out(patient: object) -> Envelope[PatientOut]:
    return Envelope[PatientOut](data=PatientOut.model_validate(patient))


@router.get("", response_model=Envelope[list[PatientOut]], summary="List patients")
def list_patients(
    db: DbSession,
    last_name: Annotated[str | None, Query(description="Exact match, case-insensitive")] = None,
    date_of_birth: Annotated[str | None, Query(description="MM/DD/YYYY or YYYY-MM-DD")] = None,
    phone_number: Annotated[str | None, Query(description="Any format, e.g. (212) 555-0123")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Envelope[list[PatientOut]]:
    """List active (not deleted) patients, newest first."""
    patients = service.list_patients(
        db,
        last_name=" ".join(last_name.split()) if last_name else None,
        date_of_birth=_parse_filter(date_of_birth, validators.parse_date, "date_of_birth"),
        phone_number=_parse_filter(phone_number, validators.normalize_phone, "phone_number"),
        limit=limit,
        offset=offset,
    )
    return Envelope[list[PatientOut]](data=[PatientOut.model_validate(p) for p in patients])


@router.get("/{patient_id}", response_model=Envelope[PatientOut], summary="Get a patient")
def get_patient(patient_id: str, db: DbSession) -> Envelope[PatientOut]:
    """Fetch one active patient by ID."""
    return _out(service.get_patient(db, parse_patient_id(patient_id)))


@router.post(
    "",
    response_model=Envelope[PatientOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
    summary="Create a patient",
)
def create_patient(body: PatientCreate, db: DbSession) -> Envelope[PatientOut]:
    """Register a new patient. Requires the X-API-Key header."""
    return _out(service.create_patient(db, body))


@router.put(
    "/{patient_id}",
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
    summary="Update a patient (partial)",
)
def update_patient(patient_id: str, body: PatientUpdate, db: DbSession) -> Envelope[PatientOut]:
    """Update only the fields provided. Required fields cannot be cleared. Requires X-API-Key."""
    return _out(service.update_patient(db, parse_patient_id(patient_id), body.changes()))


@router.delete(
    "/{patient_id}",
    response_model=Envelope[PatientOut],
    dependencies=[Depends(require_api_key)],
    summary="Soft-delete a patient",
)
def delete_patient(patient_id: str, db: DbSession) -> Envelope[PatientOut]:
    """Soft-delete a patient (sets deleted_at). Requires X-API-Key."""
    return _out(service.delete_patient(db, parse_patient_id(patient_id)))
