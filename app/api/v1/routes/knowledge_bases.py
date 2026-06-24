from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import knowledge_base as kb_crud
from app.models.common import Page
from app.models.knowledge_base import (
    KnowledgeBasePageCreate,
    KnowledgeBasePagePublic,
    KnowledgeBasePageUpdate,
)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _require_perm(ctx: ActiveOrgContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


@router.get("/", response_model=Page[KnowledgeBasePagePublic])
async def list_kb_pages(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
) -> Page[KnowledgeBasePagePublic]:
    _require_perm(ctx, "read:knowledge_base")
    pages, total = await kb_crud.list_pages(
        session, ctx.organisation_id, skip=skip, limit=limit, search=search
    )
    return Page(
        items=[KnowledgeBasePagePublic.model_validate(p, from_attributes=True) for p in pages],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=KnowledgeBasePagePublic, status_code=status.HTTP_201_CREATED)
async def create_kb_page(
    page_in: KnowledgeBasePageCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.create_page(
        session,
        page_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return KnowledgeBasePagePublic.model_validate(page, from_attributes=True)


@router.patch("/{page_id}", response_model=KnowledgeBasePagePublic)
async def update_kb_page(
    page_id: int,
    page_in: KnowledgeBasePageUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    page = await kb_crud.update_page(
        session, page, page_in, updated_by=str(ctx.user.id)
    )
    return KnowledgeBasePagePublic.model_validate(page, from_attributes=True)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_page(
    page_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    await kb_crud.delete_page(session, page, deleted_by=str(ctx.user.id))
