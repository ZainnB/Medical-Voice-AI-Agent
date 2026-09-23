"""Demo data: two fictional patients (555-01xx numbers are reserved for fiction)."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Patient
from app.schemas import PatientCreate
from app.services import patients as service

logger = logging.getLogger(__name__)

DEMO_PATIENTS = [
    PatientCreate(
        first_name="Maria",
        last_name="Gonzalez",
        date_of_birth="07/14/1985",
        sex="Female",
        phone_number="5125550101",
        email="maria.gonzalez@example.com",
        address_line_1="742 Evergreen Ter",
        city="Austin",
        state="TX",
        zip_code="78701",
        insurance_provider="Blue Cross Blue Shield",
        insurance_member_id="BCB123456789",
        preferred_language="Spanish",
        emergency_contact_name="Luis Gonzalez",
        emergency_contact_phone="5125550102",
    ),
    PatientCreate(
        first_name="James",
        last_name="O'Connor",
        date_of_birth="11/02/1962",
        sex="Male",
        phone_number="6175550103",
        address_line_1="15 Beacon St",
        address_line_2="Unit 3",
        city="Boston",
        state="MA",
        zip_code="02108",
    ),
]


def seed_demo_patients(db: Session) -> int:
    """Insert the demo patients if the patients table is empty. Returns how many were added."""
    if db.scalar(select(func.count()).select_from(Patient)):
        return 0
    for patient in DEMO_PATIENTS:
        service.create_patient(db, patient)
    logger.info("seeded_demo_patients", extra={"fields": {"count": len(DEMO_PATIENTS)}})
    return len(DEMO_PATIENTS)
