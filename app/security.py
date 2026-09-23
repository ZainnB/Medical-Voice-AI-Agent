"""Authentication dependencies: API key for REST writes."""

import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.config import get_settings

_api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Required for POST, PUT, and DELETE on /patients."
)


def secrets_match(provided: str | None, expected: str) -> bool:
    """Constant-time comparison. An unset expected secret never matches (fail closed)."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Reject the request with 401 unless it carries the correct X-API-Key header."""
    if not secrets_match(api_key, get_settings().api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid API key (X-API-Key header)")
