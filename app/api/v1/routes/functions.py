from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import function as func_crud
from app.models.common import Page
from app.models.function import (
    FunctionCreate,
    FunctionPublic,
    FunctionUpdate,
)

router = APIRouter(prefix="/functions", tags=["functions"])


def _require_perm(ctx: ActiveOrgContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


@router.get("/", response_model=Page[FunctionPublic])
async def list_functions(
    ctx: ActiveOrgContext,
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
    ctx: ActiveOrgContext,
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
    ctx: ActiveOrgContext,
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
    ctx: ActiveOrgContext,
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
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "write:function")
    func = await func_crud.get_function(session, function_id, ctx.organisation_id)
    if not func:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Function not found"
        )
    await func_crud.delete_function(session, func, deleted_by=str(ctx.user.id))
