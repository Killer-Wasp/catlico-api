import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    assert_permissions_grantable,
    get_granter_groups,
    require_active_permission,
)
from app.core.db import get_session
from app.crud import role as role_crud
from app.crud.audit import record_audit
from app.models.role import Role, RoleCreate, RolePublic, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])


async def _build_role_public(session: AsyncSession, role: Role) -> RolePublic:
    permissions = await role_crud.get_role_permissions(session, role.id)
    return RolePublic(
        id=role.id,
        organisation_id=role.organisation_id,
        name=role.name,
        permissions=permissions,
        created_at=role.created_at,
    )


async def _role_in_org_or_404(
    session: AsyncSession, role_id: uuid.UUID, organisation_id: str
) -> Role:
    role = await role_crud.get_role(session, role_id)
    if not role or role.organisation_id != organisation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return role


@router.get("/", response_model=list[RolePublic])
async def list_roles(
    ctx: Annotated[AuthContext, require_active_permission("read:role")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> list[RolePublic]:
    roles = await role_crud.get_roles(session, ctx.organisation_id, skip=skip, limit=limit)
    return [await _build_role_public(session, r) for r in roles]


@router.post("/", response_model=RolePublic, status_code=status.HTTP_201_CREATED)
async def create_role(
    ctx: Annotated[AuthContext, require_active_permission("write:role")],
    role_in: RoleCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    assert_permissions_grantable(
        {p.value for p in role_in.permissions}, await get_granter_groups(session, ctx)
    )
    existing = await role_crud.get_role_by_name(session, role_in.name, ctx.organisation_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{role_in.name}' already exists",
        )
    role = await role_crud.create_role(
        session, role_in, organisation_id=ctx.organisation_id, created_by=str(ctx.user.id)
    )
    await record_audit(
        session,
        action="create",
        obj=role,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
        details={"name": role.name, "permissions": [p.value for p in role_in.permissions]},
    )
    return await _build_role_public(session, role)


@router.get("/{role_id}", response_model=RolePublic)
async def get_role(
    role_id: uuid.UUID,
    ctx: Annotated[AuthContext, require_active_permission("read:role")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    role = await _role_in_org_or_404(session, role_id, ctx.organisation_id)
    return await _build_role_public(session, role)


@router.patch("/{role_id}", response_model=RolePublic)
async def update_role(
    role_id: uuid.UUID,
    role_in: RoleUpdate,
    ctx: Annotated[AuthContext, require_active_permission("write:role")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RolePublic:
    assert_permissions_grantable(
        {p.value for p in role_in.permissions}, await get_granter_groups(session, ctx)
    )
    role = await _role_in_org_or_404(session, role_id, ctx.organisation_id)
    role = await role_crud.update_role_permissions(
        session, role, role_in, updated_by=str(ctx.user.id)
    )
    await record_audit(
        session,
        action="update",
        obj=role,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
        details={"permissions": [p.value for p in role_in.permissions]},
    )
    return await _build_role_public(session, role)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    ctx: Annotated[AuthContext, require_active_permission("write:role")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    role = await _role_in_org_or_404(session, role_id, ctx.organisation_id)
    await record_audit(
        session,
        action="delete",
        obj=role,
        context_type="organisation",
        context_id=ctx.organisation_id,
        actor=str(ctx.user.id),
    )
    await role_crud.delete_role(session, role)
