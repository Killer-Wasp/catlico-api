import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SuperAdminUser
from app.core.db import get_session
from app.core.security import verify_password
from app.crud import user as user_crud
from app.crud.audit import record_audit
from app.models.user import UserCreate, UserMeUpdate, UserPublic, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserPublic)
async def read_current_user(current_user: CurrentUser) -> UserPublic:
    return current_user


@router.patch("/me", response_model=UserPublic)
async def update_current_user(
    body: UserMeUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserPublic:
    changing = body.email is not None or body.new_password is not None
    if changing and not body.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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

    update_data = UserUpdate()
    if body.email is not None:
        update_data.email = body.email
    if body.new_password is not None:
        update_data.password = body.new_password

    user = await user_crud.update_user(session, current_user, update_data)
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(current_user.id),
        details=update_data.model_dump(exclude_unset=True),
    )
    return user


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
    await record_audit(
        session,
        action="update",
        obj=user,
        actor=str(admin.id),
        details=user_in.model_dump(exclude_unset=True),
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
