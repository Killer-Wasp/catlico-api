import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.configs import settings

ALGORITHM = "HS256"

# JWT `type` claim values.
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

pwd_context = PasswordHash((BcryptHasher(),))

# Minimum length for a user-chosen password. Length-only policy (NIST-style):
# enforced on the backend as the source of truth and mirrored in the UI.
# Applies to every API path that sets a password (reset, user create/update);
# internal callers (seeding, crud) are exempt by design.
MIN_PASSWORD_LENGTH = 12
PASSWORD_POLICY_MESSAGE = f"Password must be at least {MIN_PASSWORD_LENGTH} characters"


def password_meets_policy(password: str) -> bool:
    return len(password) >= MIN_PASSWORD_LENGTH


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


@dataclass
class TokenPayload:
    user_id: uuid.UUID
    is_superadmin: bool
    organisations: list[str] = field(default_factory=list)


def create_access_token(payload: TokenPayload, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    data = {
        "type": ACCESS_TOKEN_TYPE,
        "user_id": str(payload.user_id),
        "is_superadmin": payload.is_superadmin,
        "organisations": payload.organisations,
        "exp": expire,
    }
    return jwt.encode(data, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(token_id: uuid.UUID, user_id: uuid.UUID, expires_at: datetime) -> str:
    data = {
        "type": REFRESH_TOKEN_TYPE,
        "token_id": str(token_id),
        "user_id": str(user_id),
        "exp": expires_at,
    }
    return jwt.encode(data, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_refresh_token(token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    try:
        raw = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if raw.get("type") != REFRESH_TOKEN_TYPE:
            return None
        return uuid.UUID(raw["token_id"]), uuid.UUID(raw["user_id"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def decode_access_token(token: str) -> TokenPayload | None:
    try:
        raw = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if raw.get("type") != ACCESS_TOKEN_TYPE:
            return None
        return TokenPayload(
            user_id=uuid.UUID(raw["user_id"]),
            is_superadmin=bool(raw.get("is_superadmin", False)),
            organisations=list(raw.get("organisations", [])),
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
