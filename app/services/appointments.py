"""First-appointment scheduling against a mock clinic calendar.

The calendar is generated, not stored: weekdays over the next two weeks, four slots a day in the
clinic's time zone, mornings with one provider and afternoons with another. Only bookings are
stored (the appointments table), and a partial unique index guarantees one booking per slot.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Appointment

SLOT_TIMES = (time(9, 0), time(10, 30), time(13, 0), time(15, 30))
MORNING_PROVIDER = "Dr. Priya Shah"
AFTERNOON_PROVIDER = "Dr. Marcus Allen"
BOOKING_WINDOW_DAYS = 14
MAX_OFFERED = 3


def clinic_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().clinic_timezone)


def as_utc(value: datetime) -> datetime:
    """Normalize to aware UTC (SQLite returns naive datetimes that were stored as UTC)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def provider_for(starts_at: datetime) -> str:
    return MORNING_PROVIDER if starts_at.astimezone(clinic_zone()).hour < 12 else AFTERNOON_PROVIDER


def spoken(starts_at: datetime) -> str:
    """'Monday, September 28 at 9 AM' / 'at 10:30 AM', in clinic time, for the agent to say."""
    local = starts_at.astimezone(clinic_zone())
    hour = local.hour % 12 or 12
    clock = f"{hour}" if local.minute == 0 else f"{hour}:{local.minute:02d}"
    return f"{local:%A}, {local:%B} {local.day} at {clock} {'AM' if local.hour < 12 else 'PM'}"


def schedule(now: datetime | None = None) -> list[datetime]:
    """Every slot in the booking window that is still in the future, earliest first."""
    zone = clinic_zone()
    now = (now or datetime.now(UTC)).astimezone(zone)
    slots = []
    for offset in range(1, BOOKING_WINDOW_DAYS + 1):
        day = now.date() + timedelta(days=offset)
        if day.weekday() >= 5:  # clinic closed on weekends
            continue
        slots.extend(datetime.combine(day, slot, tzinfo=zone) for slot in SLOT_TIMES)
    return [slot for slot in slots if slot > now]


def _booked(db: Session) -> set[datetime]:
    rows = db.scalars(select(Appointment.starts_at).where(Appointment.status == "booked"))
    return {as_utc(value) for value in rows}


def available_slots(
    db: Session, *, on: date | None = None, part_of_day: str | None = None, now: datetime | None = None
) -> list[datetime]:
    """Up to MAX_OFFERED open slots, optionally on one date and/or in the morning or afternoon."""
    booked = _booked(db)
    zone = clinic_zone()
    matches = []
    for slot in schedule(now):
        local = slot.astimezone(zone)
        if as_utc(slot) in booked or (on and local.date() != on):
            continue
        if part_of_day == "morning" and local.hour >= 12 or part_of_day == "afternoon" and local.hour < 12:
            continue
        matches.append(slot)
        if len(matches) == MAX_OFFERED:
            break
    return matches


def is_free(db: Session, starts_at: datetime) -> bool:
    """True if nobody has booked this slot yet."""
    return as_utc(starts_at) not in _booked(db)


def is_offered_slot(starts_at: datetime, now: datetime | None = None) -> bool:
    """True if the time is a real, future slot in the mock calendar."""
    return as_utc(starts_at) in {as_utc(slot) for slot in schedule(now)}


def book(
    db: Session, *, patient_id: uuid.UUID, starts_at: datetime, reason: str | None, vapi_call_id: str | None
) -> Appointment:
    """Insert a booking (flush only; the caller commits). Raises IntegrityError if the slot was just taken."""
    appointment = Appointment(
        patient_id=patient_id,
        starts_at=as_utc(starts_at),
        provider=provider_for(starts_at),
        reason=reason,
        vapi_call_id=vapi_call_id,
    )
    db.add(appointment)
    db.flush()
    return appointment


def list_appointments(
    db: Session, *, patient_id: uuid.UUID | None = None, upcoming_only: bool = False, limit: int = 50, offset: int = 0
) -> list[Appointment]:
    """Booked appointments, soonest first, optionally for one patient and/or only future ones."""
    query = select(Appointment).where(Appointment.status == "booked")
    if upcoming_only:
        query = query.where(Appointment.starts_at > datetime.now(UTC))
    if patient_id is not None:
        query = query.where(Appointment.patient_id == patient_id)
    return list(db.scalars(query.order_by(Appointment.starts_at).limit(limit).offset(offset)))
