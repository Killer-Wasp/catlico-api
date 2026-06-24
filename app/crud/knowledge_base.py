from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.knowledge_base import (
    KnowledgeBasePage,
    KnowledgeBasePageCreate,
    KnowledgeBasePageUpdate,
)


async def get_page(session: AsyncSession, page_id: int, organisation_id: str) -> KnowledgeBasePage | None:
    result = await session.execute(
        select(KnowledgeBasePage).where(
            KnowledgeBasePage.id == page_id,
            KnowledgeBasePage.organisation_id == organisation_id,
            KnowledgeBasePage.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_pages(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
    search: str | None = None,
) -> tuple[list[KnowledgeBasePage], int]:
    base = select(KnowledgeBasePage).where(
        KnowledgeBasePage.organisation_id == organisation_id,
        KnowledgeBasePage.deleted_at.is_(None),
    )
    if search:
        base = base.where(KnowledgeBasePage.title.ilike(f"%{search}%"))
    return await paginate(session, base, KnowledgeBasePage.title, skip=skip, limit=limit)


async def create_page(
    session: AsyncSession,
    page_in: KnowledgeBasePageCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> KnowledgeBasePage:
    blocks = [
        b.model_dump() if hasattr(b, "model_dump") else b
        for b in page_in.blocks
    ]
    page = KnowledgeBasePage(
        organisation_id=organisation_id,
        title=page_in.title,
        summary=page_in.summary,
        tags=page_in.tags,
        blocks=blocks,
        created_by=created_by,
    )
    session.add(page)
    await session.flush()
    return page


async def update_page(
    session: AsyncSession,
    page: KnowledgeBasePage,
    page_in: KnowledgeBasePageUpdate,
    updated_by: str,
) -> KnowledgeBasePage:
    update_data = page_in.model_dump(exclude_unset=True)
    if "blocks" in update_data:
        update_data["blocks"] = [
            b.model_dump() if hasattr(b, "model_dump") else b
            for b in update_data["blocks"]
        ]
    for k, v in update_data.items():
        setattr(page, k, v)
    page.updated_at = datetime.now(UTC)
    page.updated_by = updated_by
    session.add(page)
    await session.flush()
    return page


async def delete_page(
    session: AsyncSession, page: KnowledgeBasePage, deleted_by: str
) -> None:
    page.deleted_at = datetime.now(UTC)
    page.deleted_by = deleted_by
    session.add(page)
    await session.flush()
