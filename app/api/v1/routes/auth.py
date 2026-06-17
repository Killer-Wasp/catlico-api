from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

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
