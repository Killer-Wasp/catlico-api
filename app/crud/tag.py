from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.models.tag import Tag, Tagging, TaggableType, parse_tag, tag_to_string


async def get_or_create_tag(session: AsyncSession, text: str) -> Tag:
    """Resolve a tag string to a Tag row, creating it on first use (global vocabulary)."""
    namespace, predicate, value = parse_tag(text)
    result = await session.execute(
        select(Tag).where(
            Tag.namespace == namespace,
            Tag.predicate == predicate,
            Tag.value == value,
        )
    )
    tag = result.scalar_one_or_none()
    if tag is not None:
        return tag
    tag = Tag(namespace=namespace, predicate=predicate, value=value)
    session.add(tag)
    await session.flush()
    return tag


async def list_tags_for(
    session: AsyncSession, taggable_type: TaggableType, taggable_id: str
) -> list[Tag]:
    result = await session.execute(
        select(Tag)
        .join(Tagging, Tagging.tag_id == Tag.id)
        .where(
            Tagging.taggable_type == taggable_type,
            Tagging.taggable_id == taggable_id,
        )
        .order_by(Tag.namespace, Tag.predicate, Tag.value)
    )
    return list(result.scalars().all())


async def list_tag_strings_for(
    session: AsyncSession, taggable_type: TaggableType, taggable_id: str
) -> list[str]:
    return [tag_to_string(t) for t in await list_tags_for(session, taggable_type, taggable_id)]


async def set_tags(
    session: AsyncSession,
    taggable_type: TaggableType,
    taggable_id: str,
    tag_strings: list[str],
) -> list[Tag]:
    """Replace-semantics: the entity ends up tagged with exactly `tag_strings`."""
    # Resolve/ create all tags first (also validates/normalises the strings).
    tags = [await get_or_create_tag(session, s) for s in tag_strings]
    # Drop existing taggings for this entity, then re-create.
    await session.execute(
        delete(Tagging).where(
            Tagging.taggable_type == taggable_type,
            Tagging.taggable_id == taggable_id,
        )
    )
    seen: set[int] = set()
    for tag in tags:
        if tag.id in seen:
            continue
        seen.add(tag.id)
        session.add(
            Tagging(tag_id=tag.id, taggable_type=taggable_type, taggable_id=taggable_id)
        )
    await session.flush()
    return tags
