import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._seq import next_attachment_ids
from app.models.attachment import (
    Attachment,
    AttachmentLink,
    AttachmentPublic,
    ObservableAttachmentLink,
    ObservableAttachmentPublic,
)


def to_public(link: AttachmentLink, blob: Attachment) -> AttachmentPublic:
    return AttachmentPublic(
        id=link.id,
        public_id=link.public_id,
        case_id=link.case_id,
        attachment_id=blob.id,
        owner_type=link.owner_type,
        owner_task_id=link.owner_task_id,
        owner_log_id=link.owner_log_id,
        name=link.name,
        size=blob.size,
        content_type=blob.content_type,
        sha256=blob.sha256,
        organisation_id=link.organisation_id,
        created_at=link.created_at,
        created_by=link.created_by,
    )


def to_observable_public(
    link: ObservableAttachmentLink, blob: Attachment
) -> ObservableAttachmentPublic:
    return ObservableAttachmentPublic(
        id=link.id,
        attachment_id=blob.id,
        observable_id=link.observable_id,
        name=link.name,
        size=blob.size,
        content_type=blob.content_type,
        sha256=blob.sha256,
        organisation_id=link.organisation_id,
        created_at=link.created_at,
        created_by=link.created_by,
    )


async def get_or_create_blob(
    session: AsyncSession,
    *,
    sha256: str,
    size: int,
    content_type: str,
    created_by: str,
) -> Attachment:
    """Content-addressed: reuse the existing blob row if these bytes are already known."""
    existing = await session.execute(
        select(Attachment).where(Attachment.sha256 == sha256)
    )
    blob = existing.scalar_one_or_none()
    if blob is not None:
        return blob
    blob = Attachment(
        sha256=sha256, size=size, content_type=content_type, created_by=created_by
    )
    session.add(blob)
    await session.flush()
    return blob


# --- Case-bound links (case / task / log owners), composite (case_id, id) ---


async def create_link(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    case_id: int,
    owner_task_id: int | None = None,
    owner_log_id: int | None = None,
    name: str,
    organisation_id: str,
    created_by: str,
) -> AttachmentLink:
    (link_id,) = await next_attachment_ids(session, case_id)
    link = AttachmentLink(
        case_id=case_id,
        id=link_id,
        attachment_id=attachment_id,
        owner_task_id=owner_task_id,
        owner_log_id=owner_log_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(link)
    await session.flush()
    return link


async def get_link(
    session: AsyncSession, case_id: int, id: int
) -> AttachmentLink | None:
    """A case-bound link by its composite (case_id, id) — uniquely addresses any
    case/task/log attachment, since the counter is per-case across all owners."""
    link = await session.get(AttachmentLink, (case_id, id))
    if link is None or link.deleted_at is not None:
        return None
    return link


async def get_blob(session: AsyncSession, attachment_id: uuid.UUID) -> Attachment | None:
    return await session.get(Attachment, attachment_id)


def _owner_filter(
    case_id: int,
    *,
    owner_task_id: int | None,
    owner_log_id: int | None,
    case_only: bool,
):
    conds = [AttachmentLink.case_id == case_id, AttachmentLink.deleted_at.is_(None)]
    if owner_log_id is not None:
        conds.append(AttachmentLink.owner_log_id == owner_log_id)
        conds.append(AttachmentLink.owner_task_id == owner_task_id)
    elif owner_task_id is not None:
        conds.append(AttachmentLink.owner_task_id == owner_task_id)
        conds.append(AttachmentLink.owner_log_id.is_(None))
    elif case_only:
        conds.append(AttachmentLink.owner_task_id.is_(None))
        conds.append(AttachmentLink.owner_log_id.is_(None))
    return conds


async def list_links(
    session: AsyncSession,
    case_id: int,
    *,
    owner_task_id: int | None = None,
    owner_log_id: int | None = None,
    case_only: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[tuple[AttachmentLink, Attachment]], int]:
    """List case-bound attachment links joined to their blobs. With no owner filter,
    returns every attachment in the case; pass `case_only` for case-owned only, or an
    owner id to scope to a task/log."""
    conds = _owner_filter(
        case_id,
        owner_task_id=owner_task_id,
        owner_log_id=owner_log_id,
        case_only=case_only,
    )
    base = (
        select(AttachmentLink, Attachment)
        .join(Attachment, Attachment.id == AttachmentLink.attachment_id)
        .where(*conds)
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    stmt = base.order_by(AttachmentLink.id).offset(skip).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows], total


async def delete_link(
    session: AsyncSession, link: AttachmentLink, deleted_by: str
) -> None:
    """Soft-delete the reference. The blob itself is left in place — it is
    content-addressed and may be referenced by other links (GC is out of scope)."""
    link.deleted_at = datetime.now(UTC)
    link.deleted_by = deleted_by
    session.add(link)
    await session.flush()


# --- Observable links (UUID identity, span cases) ---


async def create_observable_link(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    observable_id: uuid.UUID,
    name: str,
    organisation_id: str,
    created_by: str,
) -> ObservableAttachmentLink:
    link = ObservableAttachmentLink(
        attachment_id=attachment_id,
        observable_id=observable_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(link)
    await session.flush()
    return link


async def list_observable_links(
    session: AsyncSession,
    observable_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[tuple[ObservableAttachmentLink, Attachment]], int]:
    base = (
        select(ObservableAttachmentLink, Attachment)
        .join(Attachment, Attachment.id == ObservableAttachmentLink.attachment_id)
        .where(
            ObservableAttachmentLink.observable_id == observable_id,
            ObservableAttachmentLink.deleted_at.is_(None),
        )
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    stmt = base.order_by(ObservableAttachmentLink.created_at).offset(skip).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows], total


async def first_observable_link(
    session: AsyncSession, observable_id: uuid.UUID
) -> tuple[ObservableAttachmentLink, Attachment] | None:
    """The single attachment for a file observable (1:1 owner)."""
    links, _ = await list_observable_links(session, observable_id, limit=1)
    return links[0] if links else None
