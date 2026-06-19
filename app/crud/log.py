import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.models.log import Log, LogCreate, LogUpdate
from app.models.task import Task


async def _case_id_for_task(session: AsyncSession, task_id: uuid.UUID) -> str | None:
    """A log's activity-feed context is its task's case."""
    task = await session.get(Task, task_id)
    return str(task.case_id) if task is not None else None


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> Log | None:
    """Returns the log only if it exists and is not soft-deleted."""
    log = await session.get(Log, log_id)
    if log is None or log.deleted_at is not None:
        return None
    return log


async def list_logs_for_task(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Log], int]:
    base = select(Log).where(Log.task_id == task_id, Log.deleted_at.is_(None))

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
    task_id: uuid.UUID,
    organisation_id: str,
    created_by: str,
) -> Log:
    log = Log(
        task_id=task_id,
        organisation_id=organisation_id,
        message=log_in.message,
        occurred_at=log_in.occurred_at,
        created_by=created_by,
    )
    # Default occurred_at to created_at when the analyst didn't backdate the entry.
    if log.occurred_at is None:
        log.occurred_at = log.created_at
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=log,
        context_type="case",
        context_id=await _case_id_for_task(session, task_id),
        actor=created_by,
    )
    return log


async def update_log(
    session: AsyncSession, log: Log, log_in: LogUpdate, updated_by: str
) -> Log:
    update_data = log_in.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    log.sqlmodel_update(update_data)
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="update",
        obj=log,
        context_type="case",
        context_id=await _case_id_for_task(session, log.task_id),
        actor=updated_by,
    )
    return log


async def delete_log(session: AsyncSession, log: Log, deleted_by: str) -> None:
    """Soft delete."""
    log.deleted_at = datetime.now(UTC)
    log.deleted_by = deleted_by
    session.add(log)
    await session.flush()
    await record_audit(
        session,
        action="delete",
        obj=log,
        context_type="case",
        context_id=await _case_id_for_task(session, log.task_id),
        actor=deleted_by,
    )
