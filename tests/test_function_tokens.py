"""D2: Scoped function token signing/verification (unit — no DB)."""
import time

import jwt
import pytest

from app.core.configs import settings
from app.services.function_tokens import (
    FUNCTION_TOKEN_TYPE,
    create_function_token,
    decode_function_token,
)


def test_round_trip_carries_scope():
    token = create_function_token(
        function_id=7,
        organisation_id="org-a",
        context_type="case",
        context_id="42",
    )
    payload = decode_function_token(token)
    assert payload is not None
    assert payload["type"] == FUNCTION_TOKEN_TYPE
    assert payload["function_id"] == 7
    assert payload["organisation_id"] == "org-a"
    assert payload["context_type"] == "case"
    assert payload["context_id"] == "42"


def test_context_is_optional():
    payload = decode_function_token(
        create_function_token(function_id=1, organisation_id="org-a")
    )
    assert payload is not None
    assert payload["context_type"] is None
    assert payload["context_id"] is None


def test_tampered_token_rejected():
    token = create_function_token(function_id=1, organisation_id="org-a")
    # Flip a character in the signature segment.
    head, body, sig = token.split(".")
    bad_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    assert decode_function_token(f"{head}.{body}.{bad_sig}") is None


def test_token_signed_with_wrong_key_rejected():
    forged = jwt.encode(
        {"type": FUNCTION_TOKEN_TYPE, "function_id": 1, "organisation_id": "org-a"},
        "not-the-real-secret-but-long-enough-32b",
        algorithm="HS256",
    )
    assert decode_function_token(forged) is None


def test_expired_token_rejected():
    # min(expiry_seconds, 300) preserves negatives, so this mints an already-expired token.
    token = create_function_token(
        function_id=1, organisation_id="org-a", expiry_seconds=-1
    )
    assert decode_function_token(token) is None


def test_wrong_type_rejected():
    # A validly-signed token that isn't a function token must not be accepted.
    other = jwt.encode(
        {"type": "user", "function_id": 1, "organisation_id": "org-a"},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    assert decode_function_token(other) is None


def test_garbage_token_rejected():
    assert decode_function_token("not-a-jwt") is None
    assert decode_function_token("") is None


def test_expiry_is_capped_at_300s():
    before = time.time()
    token = create_function_token(
        function_id=1, organisation_id="org-a", expiry_seconds=10_000
    )
    payload = decode_function_token(token)
    assert payload is not None
    # exp must be capped to now + 300s, not now + 10000s.
    assert payload["exp"] <= before + 301
