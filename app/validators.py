"""Normalization and validation rules for patient data: the single source of truth.

Every function takes raw input and returns a normalized value, or raises ValueError with a
short, human-readable message. The messages are written to be spoken by the voice agent
("Date of birth can't be in the future"), so keep them plain and free of jargon.

These functions are wired into the Pydantic schemas (app/schemas.py), so the REST API and
the Vapi tool webhook apply exactly the same rules.
"""

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email

from app.config import get_settings

T = TypeVar("T")

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")
MAX_AGE_YEARS = 130
DEFAULT_LANGUAGE = "English"

_CONTROL_OR_HTML = re.compile(r"[\x00-\x1f\x7f<>]")
# A name is runs of letters (any alphabet, so José and Nguyễn work) joined by a single
# space, hyphen, or apostrophe: "Mary Ann", "O'Brien", "Smith-Jones", "De La Cruz".
_LETTERS = r"[^\W\d_]+"
_NAME = re.compile(rf"^{_LETTERS}(?:[ '\-]{_LETTERS})*$")
_PHONE_CHARS = re.compile(r"^[\d\s().+\-]+$")
_ZIP5 = re.compile(r"^\d{5}$")
_ZIP9 = re.compile(r"^(\d{5})-?(\d{4})$")
_MEMBER_ID = re.compile(r"^[A-Z0-9\-]+$")

_SEX_ALIASES = {
    "male": "Male", "m": "Male", "man": "Male", "masculino": "Male", "hombre": "Male",
    "female": "Female", "f": "Female", "woman": "Female", "femenino": "Female", "mujer": "Female",
    "other": "Other", "otro": "Other", "otra": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "declined": "Decline to Answer", "prefer not to say": "Decline to Answer",
    "prefer not to answer": "Decline to Answer",
}

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "US Virgin Islands", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
_STATE_LOOKUP = {name.casefold(): code for code, name in US_STATES.items()} | {
    "washington dc": "DC", "virgin islands": "VI", "u.s. virgin islands": "VI",
}

_LANGUAGE_ALIASES = {
    "español": "Spanish", "espanol": "Spanish", "castellano": "Spanish",
    "inglés": "English", "ingles": "English",
}


def _as_text(value: Any, label: str) -> str:
    """Coerce input to a string with surrounding whitespace removed and inner runs collapsed."""
    if value is None:
        raise ValueError(f"{label} is required")
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise ValueError(f"{label} must be text")
    return " ".join(str(value).split())


def optional(normalize: Callable[[Any], T]) -> Callable[[Any], T | None]:
    """Wrap a normalizer so None and blank strings become None instead of an error."""

    def wrapper(value: Any) -> T | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return normalize(value)

    return wrapper


def clinic_today() -> date:
    """Today's date in the clinic's time zone (used for date-of-birth checks)."""
    return datetime.now(ZoneInfo(get_settings().clinic_timezone)).date()


def normalize_text(value: Any, label: str, max_length: int) -> str:
    """Clean a free-text value (address, city, insurer): trim, collapse spaces, block HTML/control chars."""
    text = _as_text(value, label)
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > max_length:
        raise ValueError(f"{label} must be {max_length} characters or fewer")
    if _CONTROL_OR_HTML.search(text):
        raise ValueError(f"{label} contains characters that aren't allowed")
    return text


def normalize_name(value: Any, label: str, max_length: int = 50) -> str:
    """Validate a person's name; title-case it only if it arrived all lower or all upper case."""
    name = _as_text(value, label).replace("\u2019", "'")  # curly apostrophe -> straight
    if not name:
        raise ValueError(f"{label} is required")
    if len(name) > max_length:
        raise ValueError(f"{label} must be {max_length} characters or fewer")
    if not _NAME.match(name):
        raise ValueError(f"{label} can only contain letters, spaces, hyphens, and apostrophes")
    if name == name.lower() or name == name.upper():
        name = name.title()
    return name


def parse_date(value: Any) -> date:
    """Parse MM/DD/YYYY (single-digit month/day allowed) or YYYY-MM-DD into a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _as_text(value, "Date of birth")
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        fmt = "%m/%d/%Y"
    elif re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        fmt = "%Y-%m-%d"
    else:
        raise ValueError("Date of birth must be in MM/DD/YYYY format")
    try:
        return datetime.strptime(text, fmt).date()
    except ValueError:
        raise ValueError("That date of birth doesn't look like a real date") from None


def normalize_dob(value: Any, today: date | None = None) -> date:
    """Parse a date of birth and check it is not in the future or implausibly old."""
    dob = parse_date(value)
    today = today or clinic_today()
    if dob > today:
        raise ValueError("Date of birth can't be in the future")
    if dob.year < today.year - MAX_AGE_YEARS:
        raise ValueError(f"Date of birth can't be more than {MAX_AGE_YEARS} years ago")
    return dob


def format_date(value: date) -> str:
    """Format a date as MM/DD/YYYY, the format the assessment spec uses."""
    return value.strftime("%m/%d/%Y")


def normalize_sex(value: Any) -> str:
    """Map common spellings (m, female, prefer not to say, ...) to the canonical values."""
    key = _as_text(value, "Sex").casefold()
    if key in _SEX_ALIASES:
        return _SEX_ALIASES[key]
    raise ValueError("Sex must be Male, Female, Other, or Decline to Answer")


def normalize_phone(value: Any, label: str = "Phone number") -> str:
    """Reduce a US phone number to 10 digits, dropping formatting and a leading country code 1."""
    text = _as_text(value, label)
    message = f"{label} must be a 10-digit US number"
    if not _PHONE_CHARS.match(text):
        raise ValueError(message)
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # NANP: neither the area code nor the exchange can start with 0 or 1.
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        raise ValueError(message)
    return digits


def normalize_state(value: Any) -> str:
    """Accept a 2-letter USPS code or a full state/territory name; return the code."""
    text = _as_text(value, "State")
    key = text.replace(".", "").upper()
    if key in US_STATES:
        return key
    code = _STATE_LOOKUP.get(text.casefold()) or _STATE_LOOKUP.get(text.replace(".", "").casefold())
    if code:
        return code
    raise ValueError(f"{text or 'That'} isn't a valid US state")


def normalize_zip(value: Any) -> str:
    """Accept 12345, 12345-6789, or 123456789; return 12345 or 12345-6789."""
    text = _as_text(value, "ZIP code").replace(" ", "")
    if _ZIP5.match(text):
        return text
    match = _ZIP9.match(text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    raise ValueError("ZIP code must be 5 digits or ZIP+4")


def normalize_email(value: Any) -> str:
    """Validate email syntax (no DNS lookups, to keep tool calls fast) and lowercase it."""
    text = _as_text(value, "Email")
    try:
        result = validate_email(text, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("That email address isn't valid") from None
    return result.normalized.lower()


def normalize_member_id(value: Any) -> str:
    """Strip spaces and uppercase an insurance member ID; allow letters, digits, hyphens."""
    member_id = _as_text(value, "Member ID").replace(" ", "").upper()
    if not member_id:
        raise ValueError("Member ID is required")
    if len(member_id) > 50:
        raise ValueError("Member ID must be 50 characters or fewer")
    if not _MEMBER_ID.match(member_id):
        raise ValueError("Member ID can only contain letters, numbers, and hyphens")
    return member_id


def normalize_language(value: Any) -> str:
    """Title-case a language name, mapping Spanish/English spellings (Español -> Spanish).

    Blank input falls back to the default, English.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_LANGUAGE
    text = normalize_text(value, "Preferred language", 50)
    return _LANGUAGE_ALIASES.get(text.casefold(), text.title())
