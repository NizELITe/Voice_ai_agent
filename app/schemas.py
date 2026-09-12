"""Pydantic request/response schemas.

Validation lives here as well as in the voice layer deliberately: the brief
requires the API to validate server-side and not to trust the agent.
"""
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import validators as v


class PatientBase(BaseModel):
    """Shared field definitions and normalising validators."""

    first_name: str
    last_name: str
    date_of_birth: date
    sex: Literal["Male", "Female", "Other", "Decline to Answer"]
    phone_number: str
    address_line_1: str
    city: str = Field(min_length=1, max_length=100)
    state: str
    zip_code: str

    email: Optional[str] = None
    address_line_2: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = "English"
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name")
    @classmethod
    def _first(cls, value): return v.normalise_name(value, "First name")

    @field_validator("last_name")
    @classmethod
    def _last(cls, value): return v.normalise_name(value, "Last name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _dob(cls, value): return v.normalise_date_of_birth(value)

    @field_validator("sex", mode="before")
    @classmethod
    def _sex(cls, value): return v.normalise_sex(value)

    @field_validator("phone_number", mode="before")
    @classmethod
    def _phone(cls, value): return v.normalise_phone(value)

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, value): return v.normalise_state(value)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _zip(cls, value): return v.normalise_zip(value)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value):
        return v.normalise_email(value) if value not in (None, "") else None

    @field_validator("emergency_contact_phone", mode="before")
    @classmethod
    def _ec_phone(cls, value):
        if value in (None, ""):
            return None
        return v.normalise_phone(value, "Emergency contact phone")

    @field_validator("emergency_contact_name", mode="before")
    @classmethod
    def _ec_name(cls, value):
        if value in (None, ""):
            return None
        return v.normalise_name(value, "Emergency contact name")

    @field_validator("address_line_1", mode="before")
    @classmethod
    def _addr1(cls, value):
        value = " ".join(str(value or "").split())
        if not value:
            raise v.ValidationProblem("Street address is required.")
        return value


class PatientCreate(PatientBase):
    """Body for POST /patients."""


# Field -> normaliser, reused by PatientUpdate's partial-update validator.
# Kept at module level: a leading-underscore class attribute would be captured
# by Pydantic as a private attribute and would not be subscriptable.
_UPDATE_NORMALISERS = {
    "first_name": lambda x: v.normalise_name(x, "First name"),
    "last_name": lambda x: v.normalise_name(x, "Last name"),
    "date_of_birth": v.normalise_date_of_birth,
    "sex": v.normalise_sex,
    "phone_number": v.normalise_phone,
    "state": v.normalise_state,
    "zip_code": v.normalise_zip,
    "email": v.normalise_email,
    "emergency_contact_phone": lambda x: v.normalise_phone(
        x, "Emergency contact phone"
    ),
}


class PatientUpdate(BaseModel):
    """Body for PUT /patients/:id. Every field optional - partial updates allowed."""

    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = None
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator(
        "first_name", "last_name", "date_of_birth", "sex", "phone_number",
        "state", "zip_code", "email", "emergency_contact_phone",
        mode="before",
    )
    @classmethod
    def _normalise(cls, value, info):
        if value in (None, ""):
            return None
        return _UPDATE_NORMALISERS[info.field_name](value)


class PatientRead(BaseModel):
    """Response shape. Mirrors the stored record."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: Optional[str]
    address_line_1: str
    address_line_2: Optional[str]
    city: str
    state: str
    zip_code: str
    insurance_provider: Optional[str]
    insurance_member_id: Optional[str]
    preferred_language: Optional[str]
    emergency_contact_name: Optional[str]
    emergency_contact_phone: Optional[str]
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]


class Envelope(BaseModel):
    """Consistent response envelope required by the brief."""

    data: Any = None
    error: Any = None
