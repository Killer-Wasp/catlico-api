from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import custom_field as cf_crud
from app.models.common import Page
from app.models.custom_field import (
    CustomField,
    CustomFieldCreate,
    CustomFieldPublic,
    CustomFieldUpdate,
)

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def _resolve(
    session: AsyncSession, ctx: ActiveOrgOrApiKeyContext, field_id: int
) -> CustomField:
    field = await cf_crud.get_field(session, field_id, ctx.organisation_id)
    if field is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Custom field not found"
        )
    return field


@router.get("/", response_model=Page[CustomFieldPublic])
async def list_custom_fields(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[CustomFieldPublic]:
    # Reads are open to any org member so analysts can render value editors; only
    # definition writes are gated on write:custom_field.
    fields, total = await cf_crud.list_fields(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[CustomFieldPublic.model_validate(f, from_attributes=True) for f in fields],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=CustomFieldPublic, status_code=status.HTTP_201_CREATED)
async def create_custom_field(
    field_in: CustomFieldCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomFieldPublic:
    _require_perm(ctx, "write:custom_field")
    if await cf_crud.get_field_by_name(session, field_in.name, ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A custom field named '{field_in.name}' already exists",
        )
    try:
        field = await cf_crud.create_field(
            session,
            field_in,
            organisation_id=ctx.organisation_id,
            created_by=str(ctx.user.id),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return CustomFieldPublic.model_validate(field, from_attributes=True)


@router.patch("/{field_id}", response_model=CustomFieldPublic)
async def update_custom_field(
    field_id: int,
    field_in: CustomFieldUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CustomFieldPublic:
    _require_perm(ctx, "write:custom_field")
    field = await _resolve(session, ctx, field_id)
    try:
        field = await cf_crud.update_field(
            session, field, field_in, updated_by=str(ctx.user.id)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return CustomFieldPublic.model_validate(field, from_attributes=True)


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_field(
    field_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "write:custom_field")
    field = await _resolve(session, ctx, field_id)
    await cf_crud.delete_field(session, field, deleted_by=str(ctx.user.id))
