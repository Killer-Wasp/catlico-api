"""Password reset token store.

Pure DB operations for the reset flow: mint a single-use token (only its sha256
hash is persisted), atomically claim it, and the throttle / invalidation
queries. Orchestration (delivery, audit, session revocation) lives in
`app.services.password_reset`.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, update
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
    """Mint a token: return the raw value (to email) and persist only its hash.
    Opportunistically drops the user's dead rows (used or expired) so the table
    doesn't accumulate them — there is no separate purge job."""
    now = datetime.now(UTC)
    await session.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            or_(
                PasswordResetToken.used_at.is_not(None),
                PasswordResetToken.expires_at < now,
            ),
        )
    )
    raw_token = secrets.token_urlsafe(32)
    row = PasswordResetToken(
        user_id=user_id,
        token_hash=_hash(raw_token),
        expires_at=now + RESET_TOKEN_TTL,
    )
    session.add(row)
    await session.flush()
    return raw_token, row


async def consume_active_token(
    session: AsyncSession, raw_token: str
) -> PasswordResetToken | None:
    """Atomically claim the token behind `raw_token`: a single
    `UPDATE … WHERE used_at IS NULL … RETURNING`, so two concurrent resets with
    the same token can never both succeed. Returns None when the token is
    unknown, already used, or expired (an expired token is still marked used by
    the claim, which is harmless — it was unusable anyway)."""
    result = await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == _hash(raw_token),
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(PasswordResetToken)
    )
    token = result.scalar_one_or_none()
    if token is None or _as_aware(token.expires_at) < datetime.now(UTC):
        return None
    return token


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
    return result.scalars().first() is not None


async def invalidate_user_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Mark every outstanding (unused) token for the user as used, so a completed
    reset can't be replayed with a second, still-outstanding link."""
    await session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )
    await session.flush()
