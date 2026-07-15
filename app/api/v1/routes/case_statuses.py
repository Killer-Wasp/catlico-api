from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import case_status as cs_crud
from app.models.case_status import (
    CaseStatus,
    CaseStatusCreate,
    CaseStatusPublic,
    CaseStatusUpdate,
)

router = APIRouter(prefix="/case-statuses", tags=["case-statuses"])


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def _resolve(
    session: AsyncSession, ctx: ActiveOrgOrApiKeyContext, status_id: int
) -> CaseStatus:
    row = await cs_crud.get_status(session, status_id, ctx.organisation_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case status not found"
        )
    return row


def _public(row: CaseStatus) -> CaseStatusPublic:
    return CaseStatusPublic.model_validate(row, from_attributes=True)


@router.get("/", response_model=list[CaseStatusPublic])
async def list_case_statuses(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CaseStatusPublic]:
    # Reads are open to any org member: analysts need the lookup to render badges,
    # the status picker, and the filter bar. Writes are admin-gated (manage:org).
    rows = await cs_crud.list_statuses(session, ctx.organisation_id)
    return [_public(r) for r in rows]


@router.post("/", response_model=CaseStatusPublic, status_code=status.HTTP_201_CREATED)
async def create_case_status(
    status_in: CaseStatusCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseStatusPublic:
    # manage:org expands to write:organisation (see app/models/role.py groups).
    _require_perm(ctx, "write:organisation")
    if await cs_crud.get_status_by_label(session, status_in.label, ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A status labelled '{status_in.label}' already exists",
        )
    row = await cs_crud.create_status(
        session,
        status_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return _public(row)


@router.patch("/{status_id}", response_model=CaseStatusPublic)
async def update_case_status(
    status_id: int,
    status_in: CaseStatusUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseStatusPublic:
    _require_perm(ctx, "write:organisation")
    row = await _resolve(session, ctx, status_id)
    update_data = status_in.model_dump(exclude_unset=True)
    # Built-ins are a stable, protected vocabulary: their label/stage can't be
    # remapped (that would silently change consumer semantics) — only cosmetic
    # colour, hidden, and position may change (mirrors the built-in-role guard).
    if row.is_builtin and (update_data.keys() & {"label", "stage"}):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in statuses cannot have their label or stage changed",
        )
    if "label" in update_data and update_data["label"] != row.label:
        clash = await cs_crud.get_status_by_label(
            session, update_data["label"], ctx.organisation_id
        )
        if clash is not None and clash.id != row.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A status labelled '{update_data['label']}' already exists",
            )
    row = await cs_crud.update_status(
        session, row, status_in, updated_by=str(ctx.user.id)
    )
    return _public(row)


@router.delete("/{status_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case_status(
    status_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "delete:organisation")
    row = await _resolve(session, ctx, status_id)
    if row.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in statuses cannot be deleted",
        )
    in_use = await cs_crud.in_use_count(session, row.id)
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Status is in use by {in_use} case(s) and cannot be deleted — "
                "hide it instead"
            ),
        )
    await cs_crud.delete_status(session, row)
