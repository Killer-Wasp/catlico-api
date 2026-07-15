import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ActiveOrgContext,
    CurrentUser,
    SuperAdminUser,
    get_granter_groups,
)
from app.api.v1.routes._files import ingest_upload
from app.core.db import get_session
from app.core.security import (
    PASSWORD_POLICY_MESSAGE,
    password_meets_policy,
    verify_password,
)
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import user as user_crud
from app.crud.audit import record_audit
from app.crud.auth import delete_all_refresh_tokens
from app.models.attachment import Attachment
from app.models.user import UserCreate, UserMeUpdate, UserPublic, UserUpdate
from app.services import password_reset

router = APIRouter(prefix="/users", tags=["users"])


def _ensure_password_policy(password: str | None) -> None:
    """Every API path that sets a password enforces the same minimum. Internal
    callers (seeding, crud) bypass this by design — the API is the boundary."""
    if password is not None and not password_meets_policy(password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=PASSWORD_POLICY_MESSAGE
        )


def _audit_details(update_data) -> dict:
    """Audit ``details`` for a user update. UserUpdate no longer carries a
    password (admins can't set one), so nothing sensitive appears here."""
    return update_data.model_dump(exclude_unset=True)


@router.get("/me", response_model=UserPublic)
async def read_current_user(current_user: CurrentUser) -> UserPublic:
    return current_user


@router.get("/me/permissions")
async def read_current_permissions(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """The caller's permissions in the active org, for client-side gating.
    ``permissions`` is the effective fine-grained set (for hiding actions);
    ``groups`` is the raw grantable groups the caller holds (bounds the API-key
    scope picker and role editor). Enforcement is still server-side on every
    route — this only hides UI."""
    return {
        "is_superadmin": ctx.user.is_superadmin,
        "organisation_id": ctx.organisation_id,
        "permissions": sorted(ctx.permissions),
        "groups": sorted(await get_granter_groups(session, ctx)),
    }


@router.patch("/me", response_model=UserPublic)
async def update_current_user(
    body: UserMeUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPublic:
    changing = body.email is not None or body.new_password is not None
    if changing and not body.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="current_password is required when changing email or password",
        )
    if changing:
        if not current_user.hashed_password or not verify_password(
            body.current_password, current_user.hashed_password
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect current password",
            )

    _ensure_password_policy(body.new_password)

    update_data = UserUpdate()
    if body.email is not None:
        update_data.email = body.email
    # Names are editable without re-entering the current password.
    fields_set = body.model_fields_set
    if "first_name" in fields_set:
        update_data.first_name = body.first_name
    if "last_name" in fields_set:
        update_data.last_name = body.last_name

    user = await user_crud.update_user(session, current_user, update_data)
    if body.new_password is not None:
        # Password is set through the dedicated helper (UserUpdate no longer
        # carries one) — it hashes, clears must_change_password, and stamps
        # updated_at. Same rule as password reset: a change invalidates every
        # existing session, so a party holding a stolen refresh token is logged
        # out. The caller keeps their short-lived access token and re-logs-in
        # when it expires.
        user = await user_crud.set_password(session, user, body.new_password)
        await delete_all_refresh_tokens(session, user.id)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(current_user.id),
        details=_audit_details(update_data),
    )
    return user


@router.put("/me/avatar", response_model=UserPublic)
async def upload_avatar(
    file: UploadFile,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> UserPublic:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Profile picture must be an image",
        )
    sha256, size, content_type = await ingest_upload(storage, file)
    blob = await attachment_crud.get_or_create_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        created_by=str(current_user.id),
    )
    user = await user_crud.set_avatar(session, current_user, blob.id)
    await record_audit(
        session, action="update", obj=user, actor=str(current_user.id),
        details={"avatar": True},
    )
    return user


@router.delete("/me/avatar", response_model=UserPublic)
async def delete_avatar(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPublic:
    user = await user_crud.set_avatar(session, current_user, None)
    await record_audit(
        session, action="update", obj=user, actor=str(current_user.id),
        details={"avatar": False},
    )
    return user


@router.get("/{user_id}/avatar")
async def get_avatar(
    user_id: uuid.UUID,
    _: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> StreamingResponse:
    user = await user_crud.get_user_by_id(session, user_id)
    if not user or user.avatar_attachment_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No avatar")
    blob = await session.get(Attachment, user.avatar_attachment_id)
    if blob is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No avatar")
    return StreamingResponse(
        storage.stream(blob.sha256),
        media_type=blob.content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/search", response_model=list[UserPublic])
async def search_users(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str = "",
    limit: int = 20,
) -> list[UserPublic]:
    """Find users by email or name for assignee pickers.

    Results are scoped to members of the active org so the picker only offers
    users who can actually be assigned.
    """
    limit = max(1, min(limit, 50))
    return await user_crud.search_users(
        session, q, limit=limit, organisation_id=ctx.organisation_id
    )


@router.get("/", response_model=list[UserPublic])
async def list_users(
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> list[UserPublic]:
    return await user_crud.get_users(session, skip=skip, limit=limit)


@router.post("/", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    admin: SuperAdminUser,
    user_in: UserCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    background_tasks: BackgroundTasks,
) -> UserPublic:
    existing = await user_crud.get_user_by_email(session, str(user_in.email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = await user_crud.create_user(session, user_in)
    await record_audit(
        session,
        action="create",
        obj=user,
        actor=str(admin.id),
        details={"email": user.email, "is_superadmin": user.is_superadmin},
    )
    # The account is always password-less; email a set-password invite (reusing
    # the reset token + /reset-password page). Delivery runs after the response.
    await password_reset.invite_new_user(session, user, background_tasks)
    return user


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(
    user_id: uuid.UUID,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPublic:
    user = await user_crud.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: uuid.UUID,
    user_in: UserUpdate,
    admin: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPublic:
    user = await user_crud.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user = await user_crud.update_user(session, user, user_in)
    # Unlock affordance: re-activating the user clears any local-login lockout
    # (failed_login_count + locked_until). This is the superadmin "unlock" — it
    # rides the existing user-admin PATCH. (Admins no longer set passwords.)
    if user_in.is_active is True:
        await user_crud.clear_lockout(session, user)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(admin.id),
        details=_audit_details(user_in),
    )
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    current_user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    user = await user_crud.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await record_audit(session, action="delete", obj=user, actor=str(current_user.id))
    await user_crud.delete_user(session, user)
