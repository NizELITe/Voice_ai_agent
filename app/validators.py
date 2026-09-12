"""Normalisation helpers shared by the REST API and the voice tool layer.

Speech-to-text produces messier input than a web form: "California" instead of
"CA", "(555) 010-1234" or "five five five..." instead of 5550101234, "March 5th
1990" instead of 03/05/1990. These helpers coerce the common spoken forms into
canonical storage values so the agent does not have to re-prompt the caller for
something a human intake coordinator would simply have understood.
"""
import re
from datetime import date, datetime

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "PR": "Puerto Rico", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}
_STATE_NAME_TO_CODE = {name.lower(): code for code, name in US_STATES.items()}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\-' ]{0,49}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

SEX_ALIASES = {
    "m": "Male", "male": "Male", "man": "Male", "boy": "Male",
    "f": "Female", "female": "Female", "woman": "Female", "girl": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "nb": "Other", "x": "Other",
    "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
    "n/a": "Decline to Answer", "none": "Decline to Answer",
    "rather not say": "Decline to Answer", "skip": "Decline to Answer",
}

# Ordered by how likely speech-to-text / an LLM is to emit them.
_DATE_FORMATS = (
    "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y",
    "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y",
    "%d %B %Y", "%d %b %Y",
)


class ValidationProblem(ValueError):
    """Raised when a value cannot be coerced into a storable form.

    The message is written to be read aloud by the agent, so it names the field
    and what specifically is wrong rather than echoing a schema error.
    """


def normalise_name(value: str, field: str) -> str:
    value = " ".join((value or "").strip().split())
    if not value:
        raise ValidationProblem(f"{field} is required.")
    if not NAME_RE.match(value):
        raise ValidationProblem(
            f"{field} may only contain letters, hyphens and apostrophes, "
            "and must be 50 characters or fewer."
        )
    return value


def normalise_phone(value: str, field: str = "Phone number") -> str:
    """Reduce any spoken/typed US phone number to its 10 significant digits."""
    digits = re.sub(r"\D", "", value or "")
    # Callers and transcribers frequently include the country code.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValidationProblem(
            f"{field} must be a 10-digit U.S. number, including the area code."
        )
    if digits[0] in "01" or digits[3] in "01":
        raise ValidationProblem(
            f"{field} does not look like a valid U.S. number - the area code and "
            "exchange cannot begin with a zero or a one."
        )
    return digits


def normalise_date_of_birth(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = value
    elif isinstance(value, datetime):
        parsed = value.date()
    else:
        raw = str(value or "").strip().replace(",", "")
        # Strip ordinal suffixes: "March 5th 1990" -> "March 5 1990"
        raw = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", raw, flags=re.IGNORECASE)
        parsed = None
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValidationProblem(
                "Date of birth must be a real date in month, day, year order."
            )
    if parsed > date.today():
        raise ValidationProblem("Date of birth cannot be in the future.")
    if parsed.year < 1900:
        raise ValidationProblem("Date of birth must be after 1900.")
    return parsed


def normalise_sex(value: str) -> str:
    key = " ".join((value or "").strip().lower().split())
    if key in SEX_ALIASES:
        return SEX_ALIASES[key]
    raise ValidationProblem(
        "Sex must be Male, Female, Other, or Decline to Answer."
    )


def normalise_state(value: str) -> str:
    """Accept either a 2-letter code or a spoken full state name."""
    raw = " ".join((value or "").strip().split())
    if len(raw) == 2 and raw.upper() in US_STATES:
        return raw.upper()
    if raw.lower() in _STATE_NAME_TO_CODE:
        return _STATE_NAME_TO_CODE[raw.lower()]
    raise ValidationProblem("State must be a valid U.S. state.")


def normalise_zip(value: str) -> str:
    raw = (value or "").strip().replace(" ", "")
    digits = re.sub(r"\D", "", raw)
    # Speech-to-text tends to drop the hyphen out of a ZIP+4.
    if len(digits) == 9:
        raw = f"{digits[:5]}-{digits[5:]}"
    elif len(digits) == 5:
        raw = digits
    if not ZIP_RE.match(raw):
        raise ValidationProblem(
            "ZIP code must be five digits, or nine in ZIP+4 format."
        )
    return raw


def normalise_email(value: str) -> str:
    raw = (value or "").strip()
    # "name at gmail dot com" is a very common transcription. These substitutions
    # must happen while the spaces are still present, otherwise the word
    # boundaries are gone and "at"/"dot" can no longer be matched.
    raw = re.sub(r"\s+at\s+", "@", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s+dot\s+", ".", raw, flags=re.IGNORECASE)
    raw = raw.replace(" ", "")
    if not EMAIL_RE.match(raw):
        raise ValidationProblem("Email address does not look valid.")
    return raw.lower()
