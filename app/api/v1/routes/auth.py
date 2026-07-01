from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.configs import settings
from app.core.db import get_session
from app.core.security import (
    TokenPayload,
    create_access_token,
    get_password_hash,
    create_refresh_token,
    decode_refresh_token,
)
from app.crud.auth import get_valid_refresh_user_id, issue_refresh_token
from app.crud.organisation_member import get_user_organisations
from app.crud.user import authenticate_user, get_user_by_id
from app.models.auth import ForgotPasswordRequest, ResetPasswordRequest
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class Token(BaseModel):
    access_token: str
    token_type: str
    # Present on login; omitted on refresh (the caller keeps its existing one).
    refresh_token: str | None = None


async def _access_token_for(session: AsyncSession, user: User) -> str:
    """Mint an access token from the user's *current* org membership."""
    organisations = await get_user_organisations(session, user.id)
    payload = TokenPayload(
        user_id=user.id,
        is_superadmin=user.is_superadmin,
        organisations=organisations,
    )
    return create_access_token(payload=payload)


@router.post("/login", response_model=Token)
async def login(
    body: LoginRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Token:
    user = await authenticate_user(session, body.email, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    access_token = await _access_token_for(session, user)
    refresh_row = await issue_refresh_token(session, user.id)
    return Token(
        access_token=access_token,
        token_type="bearer",
        refresh_token=create_refresh_token(
            refresh_row.token, refresh_row.user_id, refresh_row.expires_at
        ),
    )


@router.post("/refresh", response_model=Token)
async def refresh(
    body: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Token:
    """Exchange a valid refresh token for a fresh access token. Membership and the
    active/superadmin flags are re-read from the DB, so permission changes take
    effect on the next refresh (within ACCESS_TOKEN_EXPIRE_MINUTES) rather than
    waiting out the old token."""
    decoded = decode_refresh_token(body.refresh_token)
    if decoded is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_id, presented_user_id = decoded
    user_id = await get_valid_refresh_user_id(session, token_id, presented_user_id)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = await _access_token_for(session, user)
    return Token(access_token=access_token, token_type="bearer")


# --- Sessions (G5) ---


@router.get("/sessions")
async def list_sessions(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """List the user's active refresh tokens (sessions)."""
    from sqlmodel import select
    from app.models.auth import RefreshToken

    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.expires_at > __import__("datetime").datetime.now(__import__("datetime").UTC),
        )
    )
    tokens = result.scalars().all()
    return [
        {"id": str(t.token), "created_at": t.created_at.isoformat(), "expires_at": t.expires_at.isoformat()}
        for t in tokens
    ]


@router.delete("/sessions/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    token_id: str,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke a specific refresh token (log out a session)."""
    from sqlmodel import select
    from app.models.auth import RefreshToken

    t = await session.get(RefreshToken, __import__("uuid").UUID(token_id))
    if t is None or t.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await session.delete(t)
    await session.flush()


# --- Password Reset (G6) ---


@router.post("/password/forgot")
async def forgot_password(
    body: ForgotPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Request a password reset link. Always returns the same response."""
    import hashlib, secrets
    from datetime import UTC, datetime, timedelta
    from sqlmodel import select
    from app.models.auth import PasswordResetToken
    from app.models.user import User as UserModel
    from app.services import password_reset_delivery

    public = {"message": "If the email is registered, a reset link has been sent"}
    result = await session.execute(select(UserModel).where(UserModel.email == body.email))
    user = result.scalar_one_or_none()
    if user is None:
        return public

    now = datetime.now(UTC)
    recent_cutoff = now - timedelta(seconds=settings.PASSWORD_RESET_THROTTLE_SECONDS)
    existing = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.created_at >= recent_cutoff,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return public

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    token = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + timedelta(hours=1),
    )
    session.add(token)
    await session.flush()

    # No raw-token logging. Delivery is a no-op unless SMTP is configured.
    await password_reset_delivery.send_password_reset_email(user.email, raw_token)
    return public


@router.post("/password/reset")
async def reset_password(
    body: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reset password using a valid reset token."""
    import hashlib
    from datetime import UTC, datetime
    from sqlmodel import select
    from app.models.auth import PasswordResetToken

    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    result = await session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if token is None or token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    user = await get_user_by_id(session, token.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    user.hashed_password = get_password_hash(body.new_password)
    token.used_at = datetime.now(UTC)
    session.add(user)
    session.add(token)
    await session.flush()
    return {"message": "Password reset successfully"}
