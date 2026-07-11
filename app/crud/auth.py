import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Revoke every refresh token (session) for a user in one DELETE. Returns the
    count removed. Used on password reset so any existing sessions are logged out."""
    result = await session.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user_id)
    )
    await session.flush()
    return result.rowcount or 0


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
