import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.models.auth import RefreshToken
from app.models.user import User


async def password_reset_required_token(
    session: AsyncSession, user: User
) -> str | None:
    """Shared force-reset guard for EVERY password-path token-issuance point.

    If ``user.must_change_password`` is set, mint a single-use password-reset
    token and return its raw value; the caller must then issue NO access/refresh
    tokens and instead bounce the user to the reset page with this token. Returns
    ``None`` when the flag is clear (normal login proceeds).

    Called from the OSS login route AND the enterprise MFA/passkey verify paths,
    so a flagged user cannot obtain a session by completing a second factor. SSO/
    OIDC logins are passwordless and EXEMPT — the IdP owns the credential. Invoke
    this only AFTER the is_active/lockout checks so no new enumeration oracle
    appears (a locked/inactive flagged user still gets the generic responses)."""
    if not user.must_change_password:
        return None
    # Imported lazily to avoid a crud import cycle (password_reset -> auth).
    from app.crud import password_reset as reset_crud

    raw_token, _ = await reset_crud.create_token(session, user.id)
    return raw_token


async def issue_refresh_token(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> RefreshToken:
    row = RefreshToken(
        user_id=user_id,
        expires_at=datetime.now(UTC)
        + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        user_agent=user_agent,
        ip_address=ip_address,
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


async def rotate_refresh_token(
    session: AsyncSession,
    token_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> RefreshToken | None:
    """Single-use consume: validate the presented refresh token, delete it, and
    issue a replacement in one step. Rotation caps the value of a stolen refresh
    cookie to one use — a replayed old token is simply gone. Returns ``None``
    (issuing nothing) when the presented token is invalid or expired.

    The replacement is re-stamped with the current request's UA/IP so the
    session list tracks where the session is being used from now, not just at
    first login."""
    valid_user_id = await get_valid_refresh_user_id(session, token_id, user_id)
    if valid_user_id is None:
        return None
    row = await session.get(RefreshToken, token_id)
    if row is not None:
        await session.delete(row)
        await session.flush()
    return await issue_refresh_token(
        session, valid_user_id, user_agent=user_agent, ip_address=ip_address
    )
