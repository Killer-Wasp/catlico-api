from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import function as func_crud
from app.models.common import Page
from app.models.function import (
    FunctionCreate,
    FunctionPublic,
    FunctionRunCreate,
    FunctionRunPublic,
    FunctionUpdate,
)

router = APIRouter(prefix="/functions", tags=["functions"])


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


@router.get("/", response_model=Page[FunctionPublic])
async def list_functions(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[FunctionPublic]:
    _require_perm(ctx, "read:function")
    funcs, total = await func_crud.list_functions(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[FunctionPublic.model_validate(f, from_attributes=True) for f in funcs],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{function_id}", response_model=FunctionPublic)
async def get_function(
    function_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FunctionPublic:
    _require_perm(ctx, "read:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    return FunctionPublic.model_validate(func, from_attributes=True)


@router.post("/", response_model=FunctionPublic, status_code=status.HTTP_201_CREATED)
async def create_function(
    func_in: FunctionCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FunctionPublic:
    _require_perm(ctx, "write:function")
    func = await func_crud.create_function(
        session,
        func_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return FunctionPublic.model_validate(func, from_attributes=True)


@router.patch("/{function_id}", response_model=FunctionPublic)
async def update_function(
    function_id: int,
    func_in: FunctionUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FunctionPublic:
    _require_perm(ctx, "write:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    func = await func_crud.update_function(
        session, func, func_in, updated_by=str(ctx.user.id)
    )
    return FunctionPublic.model_validate(func, from_attributes=True)


@router.delete("/{function_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_function(
    function_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "delete:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    await func_crud.delete_function(session, func, deleted_by=str(ctx.user.id))


# --- Function Runs (D1) ---


@router.post("/{function_id}/run", response_model=FunctionRunPublic, status_code=status.HTTP_201_CREATED)
async def run_function(
    function_id: int,
    run_in: FunctionRunCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FunctionRunPublic:
    _require_perm(ctx, "run:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    if not func.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Function is disabled"
        )
    run = await func_crud.create_run(
        session,
        function_id=function_id,
        trigger="manual",
        input=run_in.input,
        context_type=run_in.context_type,
        context_id=run_in.context_id,
        dedup_key=run_in.dedup_key,
        created_by=str(ctx.user.id),
    )
    return FunctionRunPublic.model_validate(run, from_attributes=True)


@router.get("/{function_id}/runs", response_model=Page[FunctionRunPublic])
async def list_function_runs(
    function_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[FunctionRunPublic]:
    _require_perm(ctx, "read:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    runs, total = await func_crud.list_runs(session, function_id, skip=skip, limit=limit)
    return Page(
        items=[FunctionRunPublic.model_validate(r, from_attributes=True) for r in runs],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/{function_id}/toggle", response_model=FunctionPublic)
async def toggle_function(
    function_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FunctionPublic:
    _require_perm(ctx, "write:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    func = await func_crud.toggle_function(
        session, func, not func.enabled, updated_by=str(ctx.user.id)
    )
    return FunctionPublic.model_validate(func, from_attributes=True)
