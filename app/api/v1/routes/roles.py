import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SuperAdminUser
from app.core.db import get_session
from app.crud import role as role_crud
from app.crud.audit import record_audit
from app.models.role import RoleCreate, RolePublic, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])


async def _build_role_public(session: AsyncSession, role) -> RolePublic:
    permissions = await role_crud.get_role_permissions(session, role.id)
    return RolePublic(
        id=role.id,
        name=role.name,
        permissions=permissions,
        created_at=role.created_at,
    )


@router.get("/", response_model=list[RolePublic])
async def list_roles(
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> list[RolePublic]:
    roles = await role_crud.get_roles(session, skip=skip, limit=limit)
    return [await _build_role_public(session, r) for r in roles]


@router.post("/", response_model=RolePublic, status_code=status.HTTP_201_CREATED)
async def create_role(
    current_user: SuperAdminUser,
    role_in: RoleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    existing = await role_crud.get_role_by_name(session, role_in.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{role_in.name}' already exists",
        )
    role = await role_crud.create_role(session, role_in, created_by=str(current_user.id))
    await record_audit(
        session,
        action="create",
        obj=role,
        actor=str(current_user.id),
        details={"name": role.name, "permissions": [p.value for p in role_in.permissions]},
    )
    return await _build_role_public(session, role)


@router.get("/{role_id}", response_model=RolePublic)
async def get_role(
    role_id: uuid.UUID,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    role = await role_crud.get_role(session, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return await _build_role_public(session, role)


@router.patch("/{role_id}", response_model=RolePublic)
async def update_role(
    role_id: uuid.UUID,
    role_in: RoleUpdate,
    current_user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    role = await role_crud.get_role(session, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    role = await role_crud.update_role_permissions(
        session, role, role_in, updated_by=str(current_user.id)
    )
    await record_audit(
        session,
        action="update",
        obj=role,
        actor=str(current_user.id),
        details={"permissions": [p.value for p in role_in.permissions]},
    )
    return await _build_role_public(session, role)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    current_user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    role = await role_crud.get_role(session, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    await record_audit(session, action="delete", obj=role, actor=str(current_user.id))
    await role_crud.delete_role(session, role)
