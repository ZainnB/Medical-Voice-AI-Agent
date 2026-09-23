"""SQLAlchemy ORM models. CHECK constraints mirror the core validation rules so the
database enforces them even if a write bypasses the Pydantic schemas."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.validators import DEFAULT_LANGUAGE, SEX_VALUES

CALL_OUTCOMES = ("created", "updated", "incomplete", "failed")


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class TimestampMixin:
    """created_at / updated_at columns, maintained by the application and the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )


class Patient(TimestampMixin, Base):
    """A registered patient. Rows are soft-deleted via `deleted_at`, never removed."""

    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint(_in_list("sex", SEX_VALUES), name="ck_patients_sex"),
        CheckConstraint("length(phone_number) = 10", name="ck_patients_phone_len"),
        CheckConstraint(
            "emergency_contact_phone IS NULL OR length(emergency_contact_phone) = 10",
            name="ck_patients_emergency_phone_len",
        ),
        CheckConstraint("length(state) = 2", name="ck_patients_state_len"),
        CheckConstraint("length(first_name) >= 1 AND length(last_name) >= 1", name="ck_patients_names"),
        # Phone is deliberately NOT unique: family members often share one number.
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
        Index("ix_patients_deleted_at", "deleted_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(50))
    last_name: Mapped[str] = mapped_column(String(50))
    date_of_birth: Mapped[date] = mapped_column(Date)
    sex: Mapped[str] = mapped_column(String(20))
    phone_number: Mapped[str] = mapped_column(String(10))
    email: Mapped[str | None] = mapped_column(String(254))
    address_line_1: Mapped[str] = mapped_column(String(200))
    address_line_2: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(CHAR(2))
    zip_code: Mapped[str] = mapped_column(String(10))
    insurance_provider: Mapped[str | None] = mapped_column(String(100))
    insurance_member_id: Mapped[str | None] = mapped_column(String(50))
    preferred_language: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_LANGUAGE, server_default=DEFAULT_LANGUAGE
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Case-insensitive last-name search (defined here because it needs the mapped column).
Index("ix_patients_last_name_lower", func.lower(Patient.last_name))


class CallLog(TimestampMixin, Base):
    """One row per Vapi call: what was collected, the outcome, and the transcript."""

    __tablename__ = "call_logs"
    __table_args__ = (
        CheckConstraint(_in_list("outcome", CALL_OUTCOMES), name="ck_call_logs_outcome"),
        Index("ix_call_logs_patient_id", "patient_id"),
        Index("ix_call_logs_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    vapi_call_id: Mapped[str] = mapped_column(String(100), unique=True)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.patient_id"))
    caller_number: Mapped[str | None] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(20), default="incomplete", server_default="incomplete")
    ended_reason: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    collected_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
