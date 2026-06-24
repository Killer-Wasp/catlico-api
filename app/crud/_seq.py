"""Per-scope id allocation for composite-keyed children.

Each parent carries a counter column (`Case.next_task_seq`,
`Case.next_attachment_seq`, `Task.next_log_seq`). Allocation is a single
`UPDATE ... RETURNING` that post-increments the counter under the row lock the
UPDATE already takes — atomic, gap-free per scope, and never reused even across
soft deletes (the counter only moves forward). No sibling scan, no regex.
"""

from sqlalchemy import ColumnElement, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case_ import Case
from app.models.task import Task


async def _claim(
    session: AsyncSession,
    counter: ColumnElement[int],
    where: ColumnElement[bool],
    count: int,
) -> list[int]:
    if count < 1:
        return []
    model = counter.class_  # the mapped class owning the counter column
    stmt = (
        update(model)
        .where(where)
        .values({counter.key: counter + count})
        .returning(counter)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise LookupError("parent row not found for id allocation")
    new_value: int = row[0]
    # Post-increment semantics: we just bumped the counter past the values we own.
    return list(range(new_value - count, new_value))


async def next_task_ids(
    session: AsyncSession, case_id: int, *, count: int = 1
) -> list[int]:
    return await _claim(session, Case.next_task_seq, Case.id == case_id, count)


async def next_attachment_ids(
    session: AsyncSession, case_id: int, *, count: int = 1
) -> list[int]:
    return await _claim(session, Case.next_attachment_seq, Case.id == case_id, count)


async def next_log_ids(
    session: AsyncSession, case_id: int, task_id: int, *, count: int = 1
) -> list[int]:
    return await _claim(
        session,
        Task.next_log_seq,
        (Task.case_id == case_id) & (Task.id == task_id),
        count,
    )
