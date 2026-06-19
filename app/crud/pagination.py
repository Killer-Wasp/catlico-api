from typing import Any, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def paginate(
    session: AsyncSession,
    base: Select[tuple[T]],
    *order_by: Any,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[T], int]:
    """Run the standard list query: COUNT over `base`, then an ordered page.

    `base` is a filtered ``select(Model)``; pass the page ordering as positional
    `order_by` columns. Returns ``(rows, total)``.
    """
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    stmt = base.order_by(*order_by).offset(skip).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return rows, total
