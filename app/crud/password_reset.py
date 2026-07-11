"""Password reset token store.

Pure DB operations for the reset flow: mint a single-use token (only its sha256
hash is persisted), look one up by the raw value, consume it, and the throttle /
invalidation queries. Orchestration (delivery, audit, session revocation) lives
in `app.services.password_reset`.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.auth import PasswordResetToken

# How long a reset link stays valid.
RESET_TOKEN_TTL = timedelta(hours=1)


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _as_aware(dt: datetime) -> datetime:
    """SQLite round-trips datetimes as naive; treat those as UTC for comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def create_token(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[str, PasswordResetToken]:
    """Mint a token: return the raw value (to email) and persist only its hash."""
    raw_token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user_id,
        token_hash=_hash(raw_token),
        expires_at=datetime.now(UTC) + RESET_TOKEN_TTL,
    )
    session.add(row)
    await session.flush()
    return raw_token, row


async def get_active_token_by_raw(
    session: AsyncSession, raw_token: str
) -> PasswordResetToken | None:
    """Return the unused, unexpired token matching `raw_token`, else None."""
    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == _hash(raw_token),
            PasswordResetToken.used_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if token is None or _as_aware(token.expires_at) < datetime.now(UTC):
        return None
    return token


async def consume_token(session: AsyncSession, token: PasswordResetToken) -> None:
    token.used_at = datetime.now(UTC)
    session.add(token)
    await session.flush()


async def has_recent_unused_token(
    session: AsyncSession, user_id: uuid.UUID, throttle_seconds: int
) -> bool:
    """True if the user already has an unused token minted within the window."""
    cutoff = datetime.now(UTC) - timedelta(seconds=throttle_seconds)
    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.created_at >= cutoff,
        )
    )
    return result.scalar_one_or_none() is not None


async def invalidate_user_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Mark every outstanding (unused) token for the user as used, so a completed
    reset can't be replayed with a second, still-outstanding link."""
    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
    )
    now = datetime.now(UTC)
    for token in result.scalars().all():
        token.used_at = now
        session.add(token)
    await session.flush()
