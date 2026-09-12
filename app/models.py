"""SQLAlchemy ORM model for the patient demographic record."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import CheckConstraint, Date, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    """Standard U.S. minimum demographic dataset for patient registration.

    Constraints are enforced at the database level as well as in Pydantic so a
    bad write cannot sneak in through a path that skips schema validation.
    """

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # --- Required demographics -------------------------------------------
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(20), nullable=False)
    # Stored normalised to 10 digits so lookups by phone are exact matches.
    phone_number: Mapped[str] = mapped_column(String(10), nullable=False)
    address_line_1: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    zip_code: Mapped[str] = mapped_column(String(10), nullable=False)

    # --- Optional demographics -------------------------------------------
    email: Mapped[str | None] = mapped_column(String(255))
    address_line_2: Mapped[str | None] = mapped_column(String(100))
    insurance_provider: Mapped[str | None] = mapped_column(String(120))
    insurance_member_id: Mapped[str | None] = mapped_column(String(60))
    preferred_language: Mapped[str | None] = mapped_column(
        String(60), server_default=text("'English'"), default="English"
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(120))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(10))

    # --- Audit ------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    # Soft delete: DELETE /patients/:id stamps this instead of removing the row.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "sex IN ('Male', 'Female', 'Other', 'Decline to Answer')",
            name="ck_patients_sex",
        ),
        CheckConstraint("length(phone_number) = 10", name="ck_patients_phone_len"),
        CheckConstraint("length(state) = 2", name="ck_patients_state_len"),
        Index("ix_patients_phone_number", "phone_number"),
        Index("ix_patients_last_name", "last_name"),
        Index("ix_patients_date_of_birth", "date_of_birth"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Patient {self.patient_id} {self.first_name} {self.last_name}>"
