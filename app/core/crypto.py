"""Symmetric encryption for connector secrets at rest."""

import base64
import hashlib
import json
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.configs import settings

logger = logging.getLogger(__name__)


def derive_fernet_key(key: str) -> bytes:
    """Turn an arbitrary non-empty string into a valid Fernet key.

    Fernet requires a 32-byte url-safe base64 key. Rather than force operators
    to paste a pre-generated key, we derive one deterministically from whatever
    string they set (SHA-256 → 32 bytes → url-safe base64). The same input
    always yields the same key, so secrets stay decryptable across restarts.
    """
    digest = hashlib.sha256(key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    key = settings.SECRET_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("SECRET_ENCRYPTION_KEY must be set.")
    return Fernet(derive_fernet_key(key))


def encrypt_secrets(secrets: dict) -> str | None:
    """Encrypt a dict of connector secrets to an opaque string, or None if empty."""
    if not secrets:
        return None
    payload = json.dumps(secrets).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_secrets(blob: str | None) -> dict:
    """Inverse of encrypt_secrets. Returns {} for None/unreadable blobs."""
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(blob.encode()).decode())
    except (InvalidToken, ValueError):
        logger.error("Failed to decrypt connector secret (wrong key or corrupt data).")
        return {}


def encrypt_string(value: str | None) -> str | None:
    """Encrypt a single opaque string (e.g. a runner push-signing secret)."""
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_string(blob: str | None) -> str | None:
    """Inverse of encrypt_string. Returns None for None/unreadable blobs."""
    if not blob:
        return None
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, ValueError):
        logger.error("Failed to decrypt string (wrong key or corrupt data).")
        return None


def generate_key() -> str:
    """Helper for ops: mint a new Fernet key."""
    return Fernet.generate_key().decode()
