import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.models.auth import RefreshToken


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> RefreshToken:
    row = RefreshToken(
        user_id=user_id,
        expires_at=datetime.now(UTC)
        + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
    )
    session.add(row)
    await session.flush()
    return row


async def delete_all_refresh_tokens(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every refresh token (session) for a user. Returns the count removed.
    Used on password reset so any existing sessions are logged out."""
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id)
    )
    rows = list(result.scalars().all())
    for row in rows:
        await session.delete(row)
    await session.flush()
    return len(rows)


async def get_valid_refresh_user_id(
    session: AsyncSession, token_id: uuid.UUID, user_id: uuid.UUID
) -> uuid.UUID | None:
    row = await session.get(RefreshToken, token_id)
    if row is None:
        return None
    if row.user_id != user_id:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        await session.delete(row)
        await session.flush()
        return None
    return row.user_id
