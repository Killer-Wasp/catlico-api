"""D2: Scoped function tokens for secure execution.

Functions must not inherit their triggering user's full rights. Instead, the
runner mints a short-lived, narrowly-scoped token that the function's code
receives to call back into the Catlico API.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.configs import settings

ALGORITHM = "HS256"
FUNCTION_TOKEN_TYPE = "function"


def create_function_token(
    *,
    function_id: int,
    organisation_id: str,
    context_type: str | None = None,
    context_id: str | None = None,
    expiry_seconds: int = 15,
) -> str:
    """Mint a short-lived token scoped to one function and optionally one context
    entity. The token carries the function's org membership but NOT the triggering
    user's identity — the function runs as its own principal.

    ponytail: symmetric HS256, same as user JWTs. Per-function signing keys if
    the threat model needs isolation between functions.
    """
    now = datetime.now(UTC)
    data = {
        "type": FUNCTION_TOKEN_TYPE,
        "function_id": function_id,
        "organisation_id": organisation_id,
        "context_type": context_type,
        "context_id": context_id,
        "iat": now,
        "exp": now + timedelta(seconds=min(expiry_seconds, 300)),
    }
    return jwt.encode(data, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_function_token(token: str) -> dict | None:
    """Verify and decode a function-scoped token. Returns payload dict or None."""
    try:
        raw = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if raw.get("type") != FUNCTION_TOKEN_TYPE:
            return None
        return raw
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
