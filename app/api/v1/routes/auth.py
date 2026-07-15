import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user
from app.core.configs import settings
from app.core.db import get_session
from app.core.extensions import IdentityProvider, registry
from app.core.security import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.crud.auth import (
    issue_refresh_token,
    password_reset_required_token,
    rotate_refresh_token,
)
from app.crud.organisation_member import get_user_organisations
from app.crud.user import authenticate_user, get_user_by_id
from app.models.auth import (
    IP_ADDRESS_MAX_LENGTH,
    USER_AGENT_MAX_LENGTH,
    ForgotPasswordRequest,
    RefreshToken,
    ResetPasswordRequest,
    SessionPublic,
)
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


def clear_refresh_cookie(response: Response) -> None:
    # Attributes must match set_refresh_cookie or browsers treat it as a
    # different cookie and keep the original.
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=settings.COOKIE_SECURE,
    )


def enforce_csrf(request: Request) -> None:
    """CSRF guard for the cookie-authenticated endpoints (refresh/logout only —
    every other route authenticates via the Authorization header and needs none).

    A cross-site HTML form cannot set custom headers, and a cross-origin fetch
    that tries must first pass CORS preflight — so requiring X-Requested-With
    blocks classic CSRF. The Origin allowlist is defence in depth on top.
    """
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing CSRF header")
    origin = request.headers.get("Origin")
    if origin is not None and origin.rstrip("/") not in settings.all_cors_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")


RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def request_user_agent(request: Request) -> str | None:
    """The client's User-Agent, truncated before it ever reaches the DB. The
    header is attacker-controlled, so we cap its length defensively."""
    ua = request.headers.get("User-Agent")
    return ua[:USER_AGENT_MAX_LENGTH] if ua else None


def request_client_ip(request: Request) -> str | None:
    """Best-effort client IP for the session list.

    The app has no dedicated forwarded-IP config, so we keep it simple: when a
    proxy sets X-Forwarded-For, the *leftmost* hop is the original client (each
    proxy appends its peer to the right), so we take that; otherwise fall back to
    the direct socket peer. This value is informational only (shown in the
    session list) and never used for authorization, so trusting the header here
    is acceptable."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        leftmost = forwarded.split(",")[0].strip()
        if leftmost:
            # Attacker-controlled header — truncate defensively before storing.
            return leftmost[:IP_ADDRESS_MAX_LENGTH]
    return request.client.host if request.client else None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


def password_reset_required_response(reset_token: str) -> JSONResponse:
    """The force-reset login response: a flagged user proved their password but
    gets NO session — only a single-use reset token to bounce to /reset-password.
    Shared by the OSS login route and the enterprise MFA/passkey verify paths so
    every password-path token-issuance point returns the same shape."""
    return JSONResponse(
        content={"password_reset_required": True, "reset_token": reset_token}
    )


async def _access_token_for(session: AsyncSession, user: User) -> str:
    """Mint an access token from the user's *current* org membership."""
    organisations = await get_user_organisations(session, user.id)
    payload = TokenPayload(
        user_id=user.id,
        is_superadmin=user.is_superadmin,
        organisations=organisations,
    )
    return create_access_token(payload=payload)


@router.get("/providers", response_model=list[IdentityProvider])
async def list_identity_providers() -> list[IdentityProvider]:
    """The SSO identity providers advertised by installed extensions (empty in
    OSS). Unauthenticated: the login page calls this before any credentials
    exist to decide whether to render SSO buttons and where to send the browser."""
    return registry.identity_providers()


@router.post("/login", response_model=Token)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Token | JSONResponse:
    user = await authenticate_user(session, body.email, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    # Extension seam: after the password succeeds but before tokens are issued,
    # an installed extension (catlico-enterprise) may demand a second factor. In
    # OSS no extension is registered, so this is always None and the flow below
    # is byte-for-byte today's behaviour.
    challenge = await registry.second_factor_challenge(user)
    if challenge is not None:
        return JSONResponse(
            content={"mfa_required": True, "pending_token": challenge.pending_token}
        )

    # Force-reset gate: a flagged user proved their password but must set a new
    # one before any session is issued. Placed AFTER is_active + the MFA challenge
    # so the flag is a pure password-path lever and adds no enumeration oracle.
    reset_token = await password_reset_required_token(session, user)
    if reset_token is not None:
        return password_reset_required_response(reset_token)

    access_token = await _access_token_for(session, user)
    refresh_row = await issue_refresh_token(
        session,
        user.id,
        user_agent=request_user_agent(request),
        ip_address=request_client_ip(request),
    )
    set_refresh_cookie(
        response,
        create_refresh_token(refresh_row.token, refresh_row.user_id, refresh_row.expires_at),
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post("/refresh", response_model=Token, dependencies=[Depends(enforce_csrf)])
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh_jwt: RefreshCookie = None,
) -> Token:
    """Exchange the refresh cookie for a fresh access token. Membership and the
    active/superadmin flags are re-read from the DB, so permission changes take
    effect on the next refresh (within ACCESS_TOKEN_EXPIRE_MINUTES) rather than
    waiting out the old token.

    The refresh token itself is rotated on every use: the presented one is
    consumed (single-use) and a replacement rides back on the cookie, so a
    stolen cookie is good for at most one exchange and a replay of the old
    value fails."""
    credential_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not refresh_jwt:
        raise credential_error
    decoded = decode_refresh_token(refresh_jwt)
    if decoded is None:
        raise credential_error
    token_id, presented_user_id = decoded
    new_row = await rotate_refresh_token(
        session,
        token_id,
        presented_user_id,
        user_agent=request_user_agent(request),
        ip_address=request_client_ip(request),
    )
    if new_row is None:
        raise credential_error
    user = await get_user_by_id(session, new_row.user_id)
    if user is None or not user.is_active:
        raise credential_error
    access_token = await _access_token_for(session, user)
    set_refresh_cookie(
        response,
        create_refresh_token(new_row.token, new_row.user_id, new_row.expires_at),
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def logout(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh_jwt: RefreshCookie = None,
) -> None:
    """Revoke the refresh session and clear its cookie. Needed server-side
    because JS cannot delete an httpOnly cookie. Idempotent: a missing or
    invalid cookie still returns 204 — the goal state is 'logged out'."""
    if refresh_jwt and (decoded := decode_refresh_token(refresh_jwt)):
        token_id, user_id = decoded
        row = await session.get(RefreshToken, token_id)
        if row is not None and row.user_id == user_id:
            await session.delete(row)
            await session.flush()
    clear_refresh_cookie(response)


# --- Sessions (G5) ---


@router.get("/sessions", response_model=list[SessionPublic])
async def list_sessions(
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    refresh_jwt: RefreshCookie = None,
) -> list[SessionPublic]:
    """List the user's active refresh tokens (sessions), each with the device
    info captured at issuance and an ``is_current`` flag.

    "Current" is the session behind the caller's own refresh cookie: we decode
    the cookie the same way /refresh does to recover its token_id and match it
    against each row. An absent or invalid cookie marks nothing current."""
    from datetime import UTC, datetime

    from sqlmodel import select

    current_token_id: uuid.UUID | None = None
    if refresh_jwt and (decoded := decode_refresh_token(refresh_jwt)):
        token_id, cookie_user_id = decoded
        # Only trust the cookie's identity if it names this same user.
        if cookie_user_id == user.id:
            current_token_id = token_id

    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    tokens = result.scalars().all()
    return [
        SessionPublic(
            id=t.token,
            created_at=t.created_at,
            expires_at=t.expires_at,
            user_agent=t.user_agent,
            ip_address=t.ip_address,
            is_current=t.token == current_token_id,
        )
        for t in tokens
    ]


@router.delete("/sessions/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    token_id: str,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Revoke a specific refresh token (log out a session)."""
    t = await session.get(RefreshToken, uuid.UUID(token_id))
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
