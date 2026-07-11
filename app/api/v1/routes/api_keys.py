from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ActiveOrgContext,
    assert_permissions_grantable,
    get_granter_groups,
)
from app.core.db import get_session
from app.crud import api_key as api_key_crud
from app.models.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyPublic, ApiKeyUpdate

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _ensure_org_admin(ctx: ActiveOrgContext) -> None:
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )


@router.get("/", response_model=list[ApiKeyPublic])
async def list_api_keys(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ApiKeyPublic]:
    _ensure_org_admin(ctx)
    keys = await api_key_crud.list_keys(session, ctx.organisation_id)
    return [ApiKeyPublic.model_validate(k, from_attributes=True) for k in keys]


@router.get("/{key_id}", response_model=ApiKeyPublic)
async def get_api_key(
    key_id: UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyPublic:
    _ensure_org_admin(ctx)
    key = await api_key_crud.get_key(session, key_id, ctx.organisation_id)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    return ApiKeyPublic.model_validate(key, from_attributes=True)


@router.post("/", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_in: ApiKeyCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyCreated:
    _ensure_org_admin(ctx)
    assert_permissions_grantable(
        set(key_in.scopes), await get_granter_groups(session, ctx)
    )
    key, plaintext = await api_key_crud.create_key(
        session,
        key_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return ApiKeyCreated(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        last_four=key.last_four,
        scopes=key.scopes,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        organisation_id=key.organisation_id,
        created_at=key.created_at,
        key=plaintext,
    )


@router.patch("/{key_id}", response_model=ApiKeyPublic)
async def update_api_key(
    key_id: UUID,
    key_in: ApiKeyUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApiKeyPublic:
    _ensure_org_admin(ctx)
    key = await api_key_crud.get_key(session, key_id, ctx.organisation_id)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    if key_in.scopes is not None:
        assert_permissions_grantable(
            set(key_in.scopes), await get_granter_groups(session, ctx)
        )
    key = await api_key_crud.update_key(
        session, key, key_in, updated_by=str(ctx.user.id)
    )
    return ApiKeyPublic.model_validate(key, from_attributes=True)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _ensure_org_admin(ctx)
    key = await api_key_crud.get_key(session, key_id, ctx.organisation_id)
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    await api_key_crud.delete_key(session, key, deleted_by=str(ctx.user.id))
