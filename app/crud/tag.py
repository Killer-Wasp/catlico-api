from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.models.tag import Tag, TagCreate, TagUpdate, Tagging, TaggableType, parse_tag, tag_to_string


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


async def tags_for_many(
    session: AsyncSession, taggable_type: TaggableType, taggable_ids: list[str]
) -> dict[str, list[str]]:
    """Bulk variant of list_tag_strings_for: tag strings keyed by taggable_id.
    One query for a whole page of entities (avoids N+1 in list endpoints)."""
    if not taggable_ids:
        return {}
    result = await session.execute(
        select(Tagging.taggable_id, Tag)
        .join(Tag, Tag.id == Tagging.tag_id)
        .where(
            Tagging.taggable_type == taggable_type,
            Tagging.taggable_id.in_(taggable_ids),
        )
        .order_by(Tag.namespace, Tag.predicate, Tag.value)
    )
    out: dict[str, list[str]] = {}
    for taggable_id, tag in result.all():
        out.setdefault(taggable_id, []).append(tag_to_string(tag))
    return out


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


async def list_all_tags(
    session: AsyncSession,
    *,
    namespace: str | None = None,
) -> list[Tag]:
    base = select(Tag).order_by(Tag.namespace, Tag.predicate, Tag.value)
    if namespace is not None:
        base = base.where(Tag.namespace == namespace)
    result = await session.execute(base)
    return list(result.scalars().all())


async def delete_tag(session: AsyncSession, tag: Tag) -> None:
    await session.delete(tag)
    await session.flush()


async def create_tag(
    session: AsyncSession,
    tag_in: TagCreate,
) -> Tag:
    existing = await session.execute(
        select(Tag).where(
            Tag.namespace == tag_in.namespace,
            Tag.predicate == tag_in.predicate,
            Tag.value == tag_in.value,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(
            f"Tag '{tag_to_string_str(tag_in.namespace, tag_in.predicate, tag_in.value)}' already exists"
        )
    tag = Tag(
        namespace=tag_in.namespace,
        predicate=tag_in.predicate,
        value=tag_in.value,
        description=tag_in.description,
        colour=tag_in.colour,
    )
    session.add(tag)
    await session.flush()
    return tag


async def update_tag(
    session: AsyncSession,
    tag: Tag,
    tag_in: TagUpdate,
) -> Tag:
    update_data = tag_in.model_dump(exclude_unset=True)
    for key, val in update_data.items():
        setattr(tag, key, val)
    session.add(tag)
    await session.flush()
    return tag


def tag_to_string_str(namespace: str, predicate: str, value: str) -> str:
    s = predicate
    if namespace:
        s = f"{namespace}:{s}"
    if value:
        s = f"{s}={value}"
    return s
