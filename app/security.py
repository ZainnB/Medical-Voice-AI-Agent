"""Authentication dependencies: API key for REST writes, shared secret for Vapi webhooks."""

import hmac

from fastapi import HTTPException, Request, Security
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


def require_vapi_secret(request: Request) -> None:
    """Reject Vapi webhooks that don't carry VAPI_WEBHOOK_SECRET.

    Vapi's Bearer Token credential sends it as `Authorization: Bearer <secret>` by default, or
    as `X-Vapi-Secret: <secret>` when configured with that header name. Both are accepted.
    """
    expected = get_settings().vapi_webhook_secret
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    bearer = token.strip() if scheme.lower() == "bearer" else None
    if not (secrets_match(request.headers.get("x-vapi-secret"), expected) or secrets_match(bearer, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid webhook secret")
