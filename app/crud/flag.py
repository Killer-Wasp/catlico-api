from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.flag import Flag, FlagEntityType


async def is_flagged(
    session: AsyncSession,
    entity_type: FlagEntityType,
    entity_id: str,
    organisation_id: str,
) -> bool:
    flag = await session.get(Flag, (entity_type, entity_id, organisation_id))
    return flag is not None


async def flagged_ids(
    session: AsyncSession,
    entity_type: FlagEntityType,
    entity_ids: Iterable[str],
    organisation_id: str,
) -> set[str]:
    """Subset of entity_ids that the org has flagged — one query for a result page."""
    ids = list(entity_ids)
    if not ids:
        return set()
    result = await session.execute(
        select(Flag.entity_id).where(
            Flag.entity_type == entity_type,
            Flag.organisation_id == organisation_id,
            Flag.entity_id.in_(ids),
        )
    )
    return set(result.scalars().all())


async def set_flag(
    session: AsyncSession,
    entity_type: FlagEntityType,
    entity_id: str,
    organisation_id: str,
    created_by: str,
) -> None:
    """Idempotent: flagging an already-flagged entity is a no-op."""
    if await is_flagged(session, entity_type, entity_id, organisation_id):
        return
    session.add(
        Flag(
            entity_type=entity_type,
            entity_id=entity_id,
            organisation_id=organisation_id,
            created_by=created_by,
        )
    )
    await session.flush()


async def unset_flag(
    session: AsyncSession,
    entity_type: FlagEntityType,
    entity_id: str,
    organisation_id: str,
) -> None:
    """Idempotent: unflagging an unflagged entity is a no-op."""
    flag = await session.get(Flag, (entity_type, entity_id, organisation_id))
    if flag is not None:
        await session.delete(flag)
        await session.flush()
