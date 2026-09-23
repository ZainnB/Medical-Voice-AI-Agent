from datetime import date

import pytest
from pydantic import ValidationError

from app import validators as v
from app.schemas import PatientCreate, PatientUpdate, validation_error_details

TODAY = date(2026, 9, 23)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  mary-ann ", "Mary-Ann"),
        ("o'brien", "O'Brien"),
        ("de la cruz", "De La Cruz"),
        ("SMITH-JONES", "Smith-Jones"),
        ("McDonald", "McDonald"),  # mixed case is kept as the caller gave it
        ("Mary   Ann", "Mary Ann"),
        ("O’Neil", "O'Neil"),
        ("José", "José"),
    ],
)
def test_name_normalization(raw: str, expected: str) -> None:
    assert v.normalize_name(raw, "First name") == expected


@pytest.mark.parametrize("raw", ["J0hn", "Anne--Marie", "-Anne", "<script>", "Dr. Smith", "a" * 51])
def test_name_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        v.normalize_name(raw, "First name")


def test_name_error_message_uses_label() -> None:
    with pytest.raises(ValueError, match="^Last name can only contain letters, spaces, hyphens, and apostrophes$"):
        v.normalize_name("Sm1th", "Last name")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("03/04/1990", date(1990, 3, 4)), ("3/4/1990", date(1990, 3, 4)), ("1990-03-04", date(1990, 3, 4))],
)
def test_dob_formats(raw: str, expected: date) -> None:
    assert v.normalize_dob(raw, today=TODAY) == expected


def test_dob_today_is_allowed() -> None:
    assert v.normalize_dob("09/23/2026", today=TODAY) == TODAY


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("09/24/2026", "Date of birth can't be in the future"),
        ("02/30/1990", "That date of birth doesn't look like a real date"),
        ("13/01/1990", "That date of birth doesn't look like a real date"),
        ("March 4 1990", "Date of birth must be in MM/DD/YYYY format"),
        ("01/01/1890", "Date of birth can't be more than 130 years ago"),
    ],
)
def test_dob_rejected(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        v.normalize_dob(raw, today=TODAY)


def test_format_date() -> None:
    assert v.format_date(date(1990, 3, 4)) == "03/04/1990"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("male", "Male"), ("M", "Male"), ("female", "Female"), ("f", "Female"), ("Other", "Other"),
        ("decline", "Decline to Answer"), ("Prefer not to say", "Decline to Answer"),
        ("Decline to Answer", "Decline to Answer"), ("femenino", "Female"),
    ],
)
def test_sex_normalization(raw: str, expected: str) -> None:
    assert v.normalize_sex(raw) == expected


def test_sex_rejected() -> None:
    with pytest.raises(ValueError, match="^Sex must be Male, Female, Other, or Decline to Answer$"):
        v.normalize_sex("unknown")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(416) 555-0123", "4165550123"),
        ("+1 212 555 0123", "2125550123"),
        ("2125550123", "2125550123"),
        ("12125550123", "2125550123"),
        ("212.555.0123", "2125550123"),
        (2125550123, "2125550123"),
    ],
)
def test_phone_normalization(raw: str | int, expected: str) -> None:
    assert v.normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "212555012",  # 9 digits
        "22125550123",  # 11 digits not starting with 1
        "5550123",  # 7 digits
        "0125550123",  # area code starts with 0
        "2121550123",  # exchange starts with 1
        "212-555-O123",  # letter O instead of zero
    ],
)
def test_phone_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="^Phone number must be a 10-digit US number$"):
        v.normalize_phone(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("tx", "TX"), ("Texas", "TX"), ("TX", "TX"), ("new york", "NY"), ("D.C.", "DC"), ("Puerto Rico", "PR")],
)
def test_state_normalization(raw: str, expected: str) -> None:
    assert v.normalize_state(raw) == expected


@pytest.mark.parametrize("raw", ["ZZ", "Texass", "Ontario", ""])
def test_state_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        v.normalize_state(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12345", "12345"), ("12345-6789", "12345-6789"), ("123456789", "12345-6789"), (" 78701 ", "78701")],
)
def test_zip_normalization(raw: str, expected: str) -> None:
    assert v.normalize_zip(raw) == expected


@pytest.mark.parametrize("raw", ["1234", "123456", "12345-678", "ABCDE"])
def test_zip_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="^ZIP code must be 5 digits or ZIP\\+4$"):
        v.normalize_zip(raw)


def test_email_lowercased() -> None:
    assert v.normalize_email("  Jane.Doe@Example.COM ") == "jane.doe@example.com"


@pytest.mark.parametrize("raw", ["not-an-email", "jane@", "jane doe@example.com"])
def test_email_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="^That email address isn't valid$"):
        v.normalize_email(raw)


def test_member_id_normalization() -> None:
    assert v.normalize_member_id("abc 123-45") == "ABC123-45"


def test_member_id_rejected() -> None:
    with pytest.raises(ValueError, match="^Member ID can only contain letters, numbers, and hyphens$"):
        v.normalize_member_id("ABC#123")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("spanish", "Spanish"), ("Español", "Spanish"), ("espanol", "Spanish"), ("mandarin", "Mandarin"), ("", "English"),
     (None, "English")],
)
def test_language_normalization(raw: str | None, expected: str) -> None:
    assert v.normalize_language(raw) == expected


@pytest.mark.parametrize("raw", ["123 Main St\x00", "<b>123 Main</b>"])
def test_free_text_blocks_html_and_control_chars(raw: str) -> None:
    with pytest.raises(ValueError, match="contains characters that aren't allowed"):
        v.normalize_text(raw, "Street address", 200)


def test_optional_treats_blank_as_none() -> None:
    normalize = v.optional(v.normalize_email)
    assert normalize("   ") is None
    assert normalize(None) is None
    assert normalize("A@B.COM") == "a@b.com"


# ---- Schema-level behaviour (the same rules applied through Pydantic) ---------------------

VALID = {
    "first_name": "jane",
    "last_name": "DOE",
    "date_of_birth": "03/04/1990",
    "sex": "f",
    "phone_number": "(212) 555-0123",
    "address_line_1": "123 Main St",
    "city": "Austin",
    "state": "Texas",
    "zip_code": "78701",
}


def test_patient_create_normalizes_everything() -> None:
    patient = PatientCreate(**VALID, email="", insurance_member_id="xy 99")
    assert patient.model_dump(mode="json") == {
        "first_name": "Jane",
        "last_name": "Doe",
        "date_of_birth": "03/04/1990",
        "sex": "Female",
        "phone_number": "2125550123",
        "email": None,
        "address_line_1": "123 Main St",
        "address_line_2": None,
        "city": "Austin",
        "state": "TX",
        "zip_code": "78701",
        "insurance_provider": None,
        "insurance_member_id": "XY99",
        "preferred_language": "English",
        "emergency_contact_name": None,
        "emergency_contact_phone": None,
    }


def test_patient_create_reports_every_bad_field() -> None:
    with pytest.raises(ValidationError) as exc:
        PatientCreate(**VALID | {"date_of_birth": "01/01/2999", "zip_code": "1", "nickname": "JJ"})
    details = {d.field: d.message for d in validation_error_details(exc.value.errors())}
    assert details == {
        "date_of_birth": "Date of birth can't be in the future",
        "zip_code": "ZIP code must be 5 digits or ZIP+4",
        "nickname": "'nickname' is not a recognized field",
    }


def test_patient_create_missing_required_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        PatientCreate(first_name="Jane")
    fields = {d.field for d in validation_error_details(exc.value.errors())}
    assert fields == {"last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code"}


def test_patient_update_only_includes_sent_fields() -> None:
    assert PatientUpdate(city=" dallas ", email=None).changes() == {"city": "dallas", "email": None}


def test_patient_update_cannot_clear_required_field() -> None:
    with pytest.raises(ValidationError) as exc:
        PatientUpdate(last_name=None)
    assert validation_error_details(exc.value.errors())[0].message == "Last name can't be removed"


def test_patient_update_requires_a_field() -> None:
    with pytest.raises(ValidationError):
        PatientUpdate()
