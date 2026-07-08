from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._seq import next_log_ids
from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.models.log import Log, LogCreate, LogUpdate


def _naive_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


async def get_log(
    session: AsyncSession, case_id: int, task_id: int, id: int
) -> Log | None:
    """Returns the log only if it exists and is not soft-deleted. Identity is the
    composite (case_id, task_id, id)."""
    log = await session.get(Log, (case_id, task_id, id))
    if log is None or log.deleted_at is not None:
        return None
    return log


async def log_counts_for_case(
    session: AsyncSession, case_id: int
) -> dict[int, int]:
    """Live (non-deleted) work-log count per task for a case, in one grouped
    query. Lets the task list show an "N logs" hint without loading each task's
    logs. Tasks with no logs are simply absent from the map."""
    rows = (
        await session.execute(
            select(Log.task_id, func.count())
            .where(Log.case_id == case_id, Log.deleted_at.is_(None))
            .group_by(Log.task_id)
        )
    ).all()
    return {task_id: count for task_id, count in rows}


async def list_logs_for_task(
    session: AsyncSession,
    case_id: int,
    task_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Log], int]:
    base = select(Log).where(
        Log.case_id == case_id, Log.task_id == task_id, Log.deleted_at.is_(None)
    )

    # Timeline order is by the analyst-set event time, falling back to entry time.
    return await paginate(
        session,
        base,
        func.coalesce(Log.occurred_at, Log.created_at),
        skip=skip,
        limit=limit,
    )


async def create_log(
    session: AsyncSession,
    log_in: LogCreate,
    *,
    case_id: int,
    task_id: int,
    organisation_id: str,
    created_by: str,
) -> Log:
    (log_id,) = await next_log_ids(session, case_id, task_id)
    log = Log(
        case_id=case_id,
        task_id=task_id,
        id=log_id,
        organisation_id=organisation_id,
        message=log_in.message,
        occurred_at=_naive_utc(log_in.occurred_at),
        created_by=created_by,
    )
    # Default occurred_at to created_at when the analyst didn't backdate the entry.
    if log.occurred_at is None:
        log.occurred_at = _naive_utc(log.created_at)
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=log,
        context_type="case",
        context_id=str(case_id),
        actor=created_by,
    )
    return log


async def update_log(
    session: AsyncSession, log: Log, log_in: LogUpdate, updated_by: str
) -> Log:
    update_data = log_in.model_dump(exclude_unset=True)
    if "occurred_at" in update_data:
        update_data["occurred_at"] = _naive_utc(update_data["occurred_at"])
    update_data["updated_at"] = datetime.now(UTC).replace(tzinfo=None)
    update_data["updated_by"] = updated_by
    log.sqlmodel_update(update_data)
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="update",
        obj=log,
        context_type="case",
        context_id=str(log.case_id),
        actor=updated_by,
    )
    return log


async def delete_log(session: AsyncSession, log: Log, deleted_by: str) -> None:
    """Soft delete."""
    log.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    log.deleted_by = deleted_by
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="delete",
        obj=log,
        context_type="case",
        context_id=str(log.case_id),
        actor=deleted_by,
    )
