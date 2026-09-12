"""Service layer.

Both the REST router and the Vapi tool router call into these functions, so the
voice agent and an API client cannot diverge in behaviour or validation. This is
the "directly invoke the same service layer" option the brief allows, and it
keeps persistence logic in exactly one place.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Patient
from app.schemas import PatientCreate, PatientUpdate

logger = logging.getLogger(__name__)


def list_patients(
    db: Session,
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
    include_deleted: bool = False,
) -> list[Patient]:
    stmt = select(Patient)
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    if last_name:
        stmt = stmt.where(Patient.last_name.ilike(last_name.strip()))
    if date_of_birth:
        stmt = stmt.where(Patient.date_of_birth == date_of_birth)
    if phone_number:
        stmt = stmt.where(Patient.phone_number == phone_number)
    return list(db.scalars(stmt.order_by(Patient.created_at.desc())))


def get_patient(
    db: Session, patient_id: str, include_deleted: bool = False
) -> Optional[Patient]:
    stmt = select(Patient).where(Patient.patient_id == patient_id)
    if not include_deleted:
        stmt = stmt.where(Patient.deleted_at.is_(None))
    return db.scalars(stmt).first()


def find_by_phone(db: Session, phone_number: str) -> Optional[Patient]:
    """Used by the voice agent for returning-caller detection."""
    stmt = (
        select(Patient)
        .where(Patient.phone_number == phone_number)
        .where(Patient.deleted_at.is_(None))
        .order_by(Patient.created_at.desc())
    )
    return db.scalars(stmt).first()


def create_patient(db: Session, payload: PatientCreate) -> Patient:
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    # Observability requirement: log the final collected payload.
    logger.info(
        "PATIENT_CREATED id=%s name=%s %s phone=%s",
        patient.patient_id, patient.first_name, patient.last_name,
        patient.phone_number,
    )
    return patient


def update_patient(
    db: Session, patient: Patient, payload: PatientUpdate
) -> Patient:
    # exclude_unset keeps this a true partial update: fields the caller did not
    # mention are left alone rather than overwritten with None.
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    logger.info(
        "PATIENT_UPDATED id=%s fields=%s",
        patient.patient_id, sorted(changes.keys()),
    )
    return patient


def soft_delete_patient(db: Session, patient: Patient) -> Patient:
    """Stamp deleted_at rather than removing the row, per the brief."""
    patient.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    logger.info("PATIENT_SOFT_DELETED id=%s", patient.patient_id)
    return patient
