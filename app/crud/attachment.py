import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.attachment import (
    Attachment,
    AttachmentLink,
    AttachmentOwnerType,
    AttachmentPublic,
)


def to_public(link: AttachmentLink, blob: Attachment) -> AttachmentPublic:
    return AttachmentPublic(
        id=link.id,
        attachment_id=blob.id,
        owner_type=link.owner_type,
        owner_id=link.owner_id,
        name=link.name,
        size=blob.size,
        content_type=blob.content_type,
        sha256=blob.sha256,
        organisation_id=link.organisation_id,
        created_at=link.created_at,
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


async def create_link(
    session: AsyncSession,
    *,
    attachment_id: uuid.UUID,
    owner_type: AttachmentOwnerType,
    owner_id: str,
    name: str,
    organisation_id: str,
    created_by: str,
) -> AttachmentLink:
    link = AttachmentLink(
        attachment_id=attachment_id,
        owner_type=owner_type,
        owner_id=owner_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(link)
    await session.flush()
    return link


async def get_link(session: AsyncSession, link_id: uuid.UUID) -> AttachmentLink | None:
    link = await session.get(AttachmentLink, link_id)
    if link is None or link.deleted_at is not None:
        return None
    return link


async def get_blob(session: AsyncSession, attachment_id: uuid.UUID) -> Attachment | None:
    return await session.get(Attachment, attachment_id)


async def list_links_for_owner(
    session: AsyncSession,
    owner_type: AttachmentOwnerType,
    owner_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[tuple[AttachmentLink, Attachment]], int]:
    base = (
        select(AttachmentLink, Attachment)
        .join(Attachment, Attachment.id == AttachmentLink.attachment_id)
        .where(
            AttachmentLink.owner_type == owner_type,
            AttachmentLink.owner_id == owner_id,
            AttachmentLink.deleted_at.is_(None),
        )
    )
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    stmt = base.order_by(AttachmentLink.created_at).offset(skip).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows], total


async def first_link_for_owner(
    session: AsyncSession, owner_type: AttachmentOwnerType, owner_id: str
) -> tuple[AttachmentLink, Attachment] | None:
    """The single attachment for a 1:1 owner (a file observable)."""
    links, _ = await list_links_for_owner(session, owner_type, owner_id, limit=1)
    return links[0] if links else None


async def delete_link(
    session: AsyncSession, link: AttachmentLink, deleted_by: str
) -> None:
    """Soft-delete the reference. The blob itself is left in place — it is
    content-addressed and may be referenced by other links (GC is out of scope)."""
    link.deleted_at = datetime.now(UTC)
    link.deleted_by = deleted_by
    session.add(link)
    await session.flush()
