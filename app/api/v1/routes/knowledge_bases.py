from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import knowledge_base as kb_crud
from app.models.common import Page
from app.models.knowledge_base import (
    KnowledgeBasePageCreate,
    KnowledgeBasePageExport,
    KnowledgeBasePageImport,
    KnowledgeBasePagePublic,
    KnowledgeBasePageUpdate,
    KnowledgeBasePageVersionPublic,
)

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


@router.get("/", response_model=Page[KnowledgeBasePagePublic])
async def list_kb_pages(
    ctx: ActiveOrgOrApiKeyContext,
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
        items=[await kb_crud.public_page(session, p) for p in pages],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=KnowledgeBasePagePublic, status_code=status.HTTP_201_CREATED)
async def create_kb_page(
    page_in: KnowledgeBasePageCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.create_page(
        session,
        page_in,
        organisation_id=ctx.organisation_id,
        actor=ctx.user,
    )
    return await kb_crud.public_page(session, page)


@router.post(
    "/import",
    response_model=KnowledgeBasePagePublic,
    status_code=status.HTTP_201_CREATED,
)
async def import_kb_page(
    document: KnowledgeBasePageImport,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.import_page(
        session,
        document,
        organisation_id=ctx.organisation_id,
        actor=ctx.user,
    )
    return await kb_crud.public_page(session, page)


@router.patch("/{page_id}", response_model=KnowledgeBasePagePublic)
async def update_kb_page(
    page_id: int,
    page_in: KnowledgeBasePageUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    page = await kb_crud.update_page(session, page, page_in, actor=ctx.user)
    return await kb_crud.public_page(session, page)


@router.get("/{page_id}/versions", response_model=list[KnowledgeBasePageVersionPublic])
async def list_kb_page_versions(
    page_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KnowledgeBasePageVersionPublic]:
    _require_perm(ctx, "read:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    versions = await kb_crud.list_versions(session, page_id, ctx.organisation_id)
    return [
        KnowledgeBasePageVersionPublic.model_validate(v, from_attributes=True)
        for v in versions
    ]


@router.get("/{page_id}/export", response_model=KnowledgeBasePageExport)
async def export_kb_page(
    page_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePageExport:
    _require_perm(ctx, "read:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    return await kb_crud.export_page(session, page)


@router.post("/{page_id}/versions/{version_id}/revert", response_model=KnowledgeBasePagePublic)
async def revert_kb_page(
    page_id: int,
    version_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBasePagePublic:
    _require_perm(ctx, "write:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    version = await kb_crud.get_version(
        session, page_id, version_id, ctx.organisation_id
    )
    if not version:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base version not found"
        )
    page = await kb_crud.revert_page(session, page, version, actor=ctx.user)
    return await kb_crud.public_page(session, page)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_page(
    page_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "delete:knowledge_base")
    page = await kb_crud.get_page(session, page_id, ctx.organisation_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base page not found"
        )
    await kb_crud.delete_page(session, page, deleted_by=str(ctx.user.id))
