"""Pydantic models for requests and responses, plus the {data, error} response envelope.

Every patient field type is built from a normalizer in app/validators.py, so PatientCreate,
PatientUpdate, and the Vapi tools all share one set of rules.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import partial
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app import validators as v


def _as_utc_iso(value: datetime) -> str:
    """Serialize timestamps as ISO 8601 UTC (SQLite drops tzinfo, so assume naive means UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


# ---- Field types (normalize on input, format on JSON output) --------------------------------

FirstName = Annotated[str, BeforeValidator(partial(v.normalize_name, label="First name"))]
LastName = Annotated[str, BeforeValidator(partial(v.normalize_name, label="Last name"))]
DateOfBirth = Annotated[
    date, BeforeValidator(v.normalize_dob), PlainSerializer(v.format_date, return_type=str, when_used="json")
]
Sex = Annotated[Literal["Male", "Female", "Other", "Decline to Answer"], BeforeValidator(v.normalize_sex)]
Phone = Annotated[str, BeforeValidator(partial(v.normalize_phone, label="Phone number"))]
StreetAddress = Annotated[str, BeforeValidator(partial(v.normalize_text, label="Street address", max_length=200))]
City = Annotated[str, BeforeValidator(partial(v.normalize_text, label="City", max_length=100))]
State = Annotated[str, BeforeValidator(v.normalize_state)]
ZipCode = Annotated[str, BeforeValidator(v.normalize_zip)]
Language = Annotated[str, BeforeValidator(v.normalize_language)]

OptionalAddressLine2 = Annotated[
    str | None, BeforeValidator(v.optional(partial(v.normalize_text, label="Address line 2", max_length=100)))
]
OptionalEmail = Annotated[str | None, BeforeValidator(v.optional(v.normalize_email))]
OptionalInsurer = Annotated[
    str | None, BeforeValidator(v.optional(partial(v.normalize_text, label="Insurance provider", max_length=100)))
]
OptionalMemberId = Annotated[str | None, BeforeValidator(v.optional(v.normalize_member_id))]
OptionalContactName = Annotated[
    str | None,
    BeforeValidator(v.optional(partial(v.normalize_name, label="Emergency contact name", max_length=100))),
]
OptionalContactPhone = Annotated[
    str | None, BeforeValidator(v.optional(partial(v.normalize_phone, label="Emergency contact phone")))
]

DateOut = Annotated[date, PlainSerializer(v.format_date, return_type=str, when_used="json")]
UtcDatetime = Annotated[datetime, PlainSerializer(_as_utc_iso, return_type=str, when_used="json")]

FIELD_LABELS = {
    "first_name": "First name",
    "last_name": "Last name",
    "date_of_birth": "Date of birth",
    "sex": "Sex",
    "phone_number": "Phone number",
    "email": "Email",
    "address_line_1": "Street address",
    "address_line_2": "Address line 2",
    "city": "City",
    "state": "State",
    "zip_code": "ZIP code",
    "insurance_provider": "Insurance provider",
    "insurance_member_id": "Member ID",
    "preferred_language": "Preferred language",
    "emergency_contact_name": "Emergency contact name",
    "emergency_contact_phone": "Emergency contact phone",
}
REQUIRED_FIELDS = (
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
)

_EXAMPLE = {
    "first_name": "Jane",
    "last_name": "Doe",
    "date_of_birth": "03/04/1990",
    "sex": "Female",
    "phone_number": "2125550123",
    "email": "jane.doe@example.com",
    "address_line_1": "123 Main St",
    "address_line_2": "Apt 4B",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "insurance_provider": "Aetna",
    "insurance_member_id": "ABC123456",
    "preferred_language": "English",
    "emergency_contact_name": "John Doe",
    "emergency_contact_phone": "2125550199",
}


# ---- Patient requests ------------------------------------------------------------------------

class PatientCreate(BaseModel):
    """A new patient. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_EXAMPLE]})

    first_name: FirstName
    last_name: LastName
    date_of_birth: DateOfBirth
    sex: Sex
    phone_number: Phone
    email: OptionalEmail = None
    address_line_1: StreetAddress
    address_line_2: OptionalAddressLine2 = None
    city: City
    state: State
    zip_code: ZipCode
    insurance_provider: OptionalInsurer = None
    insurance_member_id: OptionalMemberId = None
    preferred_language: Language = v.DEFAULT_LANGUAGE
    emergency_contact_name: OptionalContactName = None
    emergency_contact_phone: OptionalContactPhone = None


class PatientUpdate(BaseModel):
    """A partial update: only the fields present in the request change.

    Optional fields may be cleared with null; required fields may not.
    """

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"address_line_1": "500 Oak Ave", "zip_code": "78702"}]}
    )

    first_name: FirstName | None = None
    last_name: LastName | None = None
    date_of_birth: DateOfBirth | None = None
    sex: Sex | None = None
    phone_number: Phone | None = None
    email: OptionalEmail = None
    address_line_1: StreetAddress | None = None
    address_line_2: OptionalAddressLine2 = None
    city: City | None = None
    state: State | None = None
    zip_code: ZipCode | None = None
    insurance_provider: OptionalInsurer = None
    insurance_member_id: OptionalMemberId = None
    preferred_language: Language | None = None
    emergency_contact_name: OptionalContactName = None
    emergency_contact_phone: OptionalContactPhone = None

    @field_validator(*REQUIRED_FIELDS, mode="before")
    @classmethod
    def _required_fields_cannot_be_cleared(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            raise ValueError(f"{FIELD_LABELS[info.field_name or '']} can't be removed")
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> "PatientUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self

    def changes(self) -> dict[str, Any]:
        """The fields the client actually sent, normalized."""
        return self.model_dump(exclude_unset=True)


# ---- Responses -------------------------------------------------------------------------------

class PatientOut(BaseModel):
    """A patient record as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: DateOut
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    deleted_at: UtcDatetime | None


class CallLogOut(BaseModel):
    """A Vapi call as recorded by the webhooks."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vapi_call_id: str
    patient_id: uuid.UUID | None
    caller_number: str | None
    outcome: str
    ended_reason: str | None
    summary: str | None
    transcript: str | None
    collected_data: dict[str, Any] | None
    started_at: UtcDatetime | None
    ended_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class Envelope[T](BaseModel):
    """Every API response: exactly one of `data` or `error` is set."""

    data: T | None = None
    error: ErrorBody | None = None


# ---- Validation error formatting (shared by the REST API and the Vapi tools) ----------------

_LOCATION_PREFIXES = {"body", "query", "path", "header"}


def validation_error_details(errors: Sequence[Any]) -> list[ErrorDetail]:
    """Turn Pydantic errors into [{field, message}] with plain-English messages."""
    details: list[ErrorDetail] = []
    for error in errors:
        loc = [str(part) for part in error["loc"]]
        if loc and loc[0] in _LOCATION_PREFIXES:
            loc = loc[1:]
        field = ".".join(loc) or "body"
        label = FIELD_LABELS.get(field, field.replace("_", " ").capitalize())
        kind = error["type"]
        if kind == "value_error" and "error" in error.get("ctx", {}):
            message = str(error["ctx"]["error"])
        elif kind == "missing":
            message = f"{label} is required"
        elif kind == "extra_forbidden":
            message = f"'{field}' is not a recognized field"
        elif kind == "literal_error" and field == "sex":
            message = "Sex must be Male, Female, Other, or Decline to Answer"
        else:
            message = f"{label}: {error['msg']}"
        details.append(ErrorDetail(field=field, message=message))
    return details
