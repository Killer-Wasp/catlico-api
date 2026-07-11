from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.configs import settings
from app.core.db import get_session
from app.core.security import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.crud.auth import get_valid_refresh_user_id, issue_refresh_token
from app.crud.organisation_member import get_user_organisations
from app.crud.user import authenticate_user, get_user_by_id
from app.models.auth import ForgotPasswordRequest, ResetPasswordRequest
from app.models.user import User
from app.services import password_reset as password_reset_service
from app.services.password_reset import PasswordResetError

router = APIRouter(prefix="/auth", tags=["auth"])

# The refresh token rides an httpOnly cookie scoped to the auth routes: JS can
# never read it, and the browser only attaches it to /api/v1/auth/* requests.
REFRESH_COOKIE_NAME = "catlico_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(response: Response, refresh_jwt: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_jwt,
        max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class Token(BaseModel):
    access_token: str
    token_type: str


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
    response: Response,
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
    set_refresh_cookie(
        response,
        create_refresh_token(refresh_row.token, refresh_row.user_id, refresh_row.expires_at),
    )
    return Token(access_token=access_token, token_type="bearer")


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

GENERIC_FORGOT_RESPONSE = {
    "message": "If the email is registered, a reset link has been sent"
}


@router.post("/password/forgot")
async def forgot_password(
    body: ForgotPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
) -> dict:
    """Request a password reset link. Always returns the same response, so the
    endpoint can't be used to discover which emails are registered. Email
    delivery runs after the response (see the service module docstring)."""
    await password_reset_service.request_reset(session, body.email, background_tasks)
    return GENERIC_FORGOT_RESPONSE


@router.post("/password/reset")
async def reset_password(
    body: ResetPasswordRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Reset a password using a valid single-use reset token."""
    try:
        await password_reset_service.perform_reset(
            session, body.token, body.new_password
        )
    except PasswordResetError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"message": "Password reset successfully"}
