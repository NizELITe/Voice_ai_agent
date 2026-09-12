"""Insert a couple of demonstration patient records.

Run once after deploying:  python seed.py
Safe to re-run - existing records with the same phone number are skipped.
"""
import logging

from app.crud import create_patient, find_by_phone
from app.database import SessionLocal, init_db
from app.schemas import PatientCreate

logging.basicConfig(level=logging.INFO, format="%(message)s")

SEED_PATIENTS = [
    {
        "first_name": "Maria", "last_name": "Gonzalez",
        "date_of_birth": "07/14/1982", "sex": "Female",
        "phone_number": "7135550142", "email": "maria.gonzalez@example.com",
        "address_line_1": "4820 Yoakum Blvd", "address_line_2": "Apt 3B",
        "city": "Houston", "state": "TX", "zip_code": "77006",
        "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "BCBS884120117",
        "preferred_language": "Spanish",
        "emergency_contact_name": "Luis Gonzalez",
        "emergency_contact_phone": "7135550188",
    },
    {
        "first_name": "David", "last_name": "Okafor",
        "date_of_birth": "11/02/1975", "sex": "Male",
        "phone_number": "9195550107",
        "address_line_1": "118 Fayetteville St",
        "city": "Raleigh", "state": "NC", "zip_code": "27601",
        "preferred_language": "English",
    },
]


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        for record in SEED_PATIENTS:
            if find_by_phone(db, record["phone_number"]):
                logging.info("Skipping %s %s - already present",
                             record["first_name"], record["last_name"])
                continue
            patient = create_patient(db, PatientCreate(**record))
            logging.info("Seeded %s %s (%s)", patient.first_name,
                         patient.last_name, patient.patient_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
