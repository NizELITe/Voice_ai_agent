"""REST API for patient records.

Every response uses the {"data": ..., "error": ...} envelope required by the
brief, including error paths - see the exception handlers in app.main.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app import crud
from app.database import get_db
from app.schemas import PatientCreate, PatientRead, PatientUpdate
from app.validators import ValidationProblem, normalise_date_of_birth, normalise_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/patients", tags=["patients"])


def _envelope(data):
    return {"data": data, "error": None}


@router.get("", summary="List patients")
def list_patients(
    db: Session = Depends(get_db),
    last_name: Optional[str] = Query(None),
    date_of_birth: Optional[str] = Query(None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: Optional[str] = Query(None),
    include_deleted: bool = Query(False, description="Include soft-deleted records"),
):
    # Query params get the same normalisation as writes, so ?phone_number=
    # works whether the client sends 7132101234 or (713) 210-1234.
    try:
        dob = normalise_date_of_birth(date_of_birth) if date_of_birth else None
        phone = normalise_phone(phone_number) if phone_number else None
    except ValidationProblem as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    patients = crud.list_patients(
        db, last_name=last_name, date_of_birth=dob,
        phone_number=phone, include_deleted=include_deleted,
    )
    return _envelope([PatientRead.model_validate(p).model_dump(mode="json")
                      for p in patients])


@router.get("/{patient_id}", summary="Retrieve a patient by UUID")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    return _envelope(PatientRead.model_validate(patient).model_dump(mode="json"))


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a patient")
def create_patient(
    payload: PatientCreate, response: Response, db: Session = Depends(get_db)
):
    patient = crud.create_patient(db, payload)
    response.headers["Location"] = f"/patients/{patient.patient_id}"
    return _envelope(PatientRead.model_validate(patient).model_dump(mode="json"))


@router.put("/{patient_id}", summary="Update a patient (partial allowed)")
def update_patient(
    patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)
):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    if not payload.model_dump(exclude_unset=True):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No fields supplied to update."
        )
    patient = crud.update_patient(db, patient, payload)
    return _envelope(PatientRead.model_validate(patient).model_dump(mode="json"))


@router.delete("/{patient_id}", summary="Soft-delete a patient")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = crud.get_patient(db, patient_id)
    if patient is None:
        # Already soft-deleted records are excluded by get_patient, so a repeat
        # delete correctly reports 404 rather than silently succeeding.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    patient = crud.soft_delete_patient(db, patient)
    return _envelope(PatientRead.model_validate(patient).model_dump(mode="json"))
