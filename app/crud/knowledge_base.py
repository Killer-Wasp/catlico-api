from datetime import UTC, datetime
from collections.abc import Iterable

from sqlalchemy import desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.knowledge_base import (
    KnowledgeBaseContributor,
    KnowledgeBasePage,
    KnowledgeBasePageCreate,
    KnowledgeBasePageExport,
    KnowledgeBasePageImport,
    KnowledgeBasePagePublic,
    KnowledgeBasePageUpdate,
    KnowledgeBasePageVersion,
    KnowledgeBasePageVersionPublic,
    KnowledgeBaseVersionAction,
)
from app.models.user import User


SNAPSHOT_FIELDS = ("title", "summary", "tags", "content")


def page_snapshot(page: KnowledgeBasePage) -> dict:
    return {
        "title": page.title,
        "summary": page.summary,
        "tags": list(page.tags),
        "content": page.content,
    }


def changed_fields(before: dict | None, after: dict) -> list[str]:
    if before is None:
        return list(SNAPSHOT_FIELDS)
    return [field for field in SNAPSHOT_FIELDS if before.get(field) != after.get(field)]


async def next_version_number(session: AsyncSession, page_id: int) -> int:
    result = await session.execute(
        select(func.max(KnowledgeBasePageVersion.version_number)).where(
            KnowledgeBasePageVersion.page_id == page_id
        )
    )
    return int(result.scalar_one_or_none() or 0) + 1


async def record_version(
    session: AsyncSession,
    page: KnowledgeBasePage,
    *,
    action: KnowledgeBaseVersionAction,
    actor: User,
    before: dict | None = None,
    reverted_from_version_id: int | None = None,
) -> KnowledgeBasePageVersion:
    after = page_snapshot(page)
    version = KnowledgeBasePageVersion(
        page_id=page.id,
        organisation_id=page.organisation_id,
        version_number=await next_version_number(session, page.id),
        action=action,
        snapshot=after,
        changed_fields=changed_fields(before, after),
        edited_by=str(actor.id),
        edited_by_email=actor.email,
        reverted_from_version_id=reverted_from_version_id,
        created_by=str(actor.id),
    )
    session.add(version)
    await session.flush()
    return version


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
    actor: User,
    action: KnowledgeBaseVersionAction = "create",
) -> KnowledgeBasePage:
    page = KnowledgeBasePage(
        organisation_id=organisation_id,
        title=page_in.title,
        summary=page_in.summary,
        tags=page_in.tags,
        content=page_in.content,
        created_by=str(actor.id),
    )
    session.add(page)
    await session.flush()
    await record_version(session, page, action=action, actor=actor)
    return page


async def update_page(
    session: AsyncSession,
    page: KnowledgeBasePage,
    page_in: KnowledgeBasePageUpdate,
    actor: User,
) -> KnowledgeBasePage:
    before = page_snapshot(page)
    update_data = page_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(page, k, v)
    page.updated_at = datetime.now(UTC)
    page.updated_by = str(actor.id)
    session.add(page)
    await session.flush()
    await record_version(session, page, action="update", actor=actor, before=before)
    return page


async def delete_page(
    session: AsyncSession, page: KnowledgeBasePage, deleted_by: str
) -> None:
    page.deleted_at = datetime.now(UTC)
    page.deleted_by = deleted_by
    session.add(page)
    await session.flush()


async def list_versions(
    session: AsyncSession, page_id: int, organisation_id: str
) -> list[KnowledgeBasePageVersion]:
    result = await session.execute(
        select(KnowledgeBasePageVersion)
        .where(
            KnowledgeBasePageVersion.page_id == page_id,
            KnowledgeBasePageVersion.organisation_id == organisation_id,
        )
        .order_by(desc(KnowledgeBasePageVersion.version_number))
    )
    return list(result.scalars().all())


async def get_version(
    session: AsyncSession, page_id: int, version_id: int, organisation_id: str
) -> KnowledgeBasePageVersion | None:
    result = await session.execute(
        select(KnowledgeBasePageVersion).where(
            KnowledgeBasePageVersion.id == version_id,
            KnowledgeBasePageVersion.page_id == page_id,
            KnowledgeBasePageVersion.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def contributors_for_pages(
    session: AsyncSession, page_ids: Iterable[int]
) -> dict[int, list[KnowledgeBaseContributor]]:
    ids = list(page_ids)
    if not ids:
        return {}
    result = await session.execute(
        select(KnowledgeBasePageVersion)
        .where(KnowledgeBasePageVersion.page_id.in_(ids))
        .order_by(desc(KnowledgeBasePageVersion.edited_at))
    )
    contributors = {page_id: [] for page_id in ids}
    seen = {page_id: set() for page_id in ids}
    for version in result.scalars().all():
        if version.edited_by in seen[version.page_id]:
            continue
        seen[version.page_id].add(version.edited_by)
        contributors[version.page_id].append(
            KnowledgeBaseContributor(
                id=version.edited_by,
                email=version.edited_by_email,
                last_edited_at=version.edited_at,
            )
        )
    return contributors


async def public_page(session: AsyncSession, page: KnowledgeBasePage) -> KnowledgeBasePagePublic:
    contributors = (await contributors_for_pages(session, [page.id])).get(page.id, [])
    base = KnowledgeBasePagePublic.model_validate(page, from_attributes=True)
    return base.model_copy(
        update={
            "contributors": contributors,
            "last_edited_by": contributors[0] if contributors else None,
        }
    )


async def revert_page(
    session: AsyncSession,
    page: KnowledgeBasePage,
    version: KnowledgeBasePageVersion,
    actor: User,
) -> KnowledgeBasePage:
    before = page_snapshot(page)
    snapshot = version.snapshot
    page.title = snapshot["title"]
    page.summary = snapshot.get("summary", "")
    page.tags = list(snapshot.get("tags", []))
    page.content = snapshot.get("content", "")
    page.updated_at = datetime.now(UTC)
    page.updated_by = str(actor.id)
    session.add(page)
    await session.flush()
    await record_version(
        session,
        page,
        action="revert",
        actor=actor,
        before=before,
        reverted_from_version_id=version.id,
    )
    return page


async def export_page(
    session: AsyncSession, page: KnowledgeBasePage
) -> KnowledgeBasePageExport:
    """Assemble a portable JSON document: the current page plus full history."""
    return KnowledgeBasePageExport(
        page=await public_page(session, page),
        versions=[
            KnowledgeBasePageVersionPublic.model_validate(v, from_attributes=True)
            for v in await list_versions(session, page.id, page.organisation_id)
        ],
    )


async def import_page(
    session: AsyncSession,
    document: KnowledgeBasePageImport,
    *,
    organisation_id: str,
    actor: User,
) -> KnowledgeBasePage:
    """Create a fresh page from an exported document.

    A new id is minted in the importing org; the incoming version history is not
    transplanted (ids/authorship belong to the source page). The import is
    recorded as a single ``import`` version so the new page starts with a clean,
    org-attributed history.
    """
    return await create_page(
        session,
        document.page,
        organisation_id=organisation_id,
        actor=actor,
        action="import",
    )
