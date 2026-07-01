import uuid
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.function import (
    Function,
    FunctionCreate,
    FunctionRun,
    FunctionRunCreate,
    FunctionRunStatus,
    FunctionUpdate,
)


async def get_function(
    session: AsyncSession, function_id: int, organisation_id: str
) -> Function | None:
    result = await session.execute(
        select(Function).where(
            Function.id == function_id,
            Function.organisation_id == organisation_id,
            Function.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_functions(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Function], int]:
    base = select(Function).where(
        Function.organisation_id == organisation_id,
        Function.deleted_at.is_(None),
    )
    return await paginate(session, base, Function.name, skip=skip, limit=limit)


async def create_function(
    session: AsyncSession,
    func_in: FunctionCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> Function:
    func = Function(
        organisation_id=organisation_id,
        name=func_in.name,
        description=func_in.description,
        runtime=func_in.runtime,
        trigger=func_in.trigger,
        trigger_config=func_in.trigger_config,
        profile=func_in.profile,
        enabled=func_in.enabled,
        timeout_ms=func_in.timeout_ms,
        egress=func_in.egress,
        approval=func_in.approval,
        code=func_in.code,
        secrets=func_in.secrets,
        created_by=created_by,
    )
    session.add(func)
    await session.flush()
    return func


async def update_function(
    session: AsyncSession,
    func: Function,
    func_in: FunctionUpdate,
    updated_by: str,
) -> Function:
    update_data = func_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(func, k, v)
    func.updated_at = datetime.now(UTC).replace(tzinfo=None)
    func.updated_by = updated_by
    session.add(func)
    await session.flush()
    return func


async def delete_function(
    session: AsyncSession, func: Function, deleted_by: str
) -> None:
    func.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    func.deleted_by = deleted_by
    session.add(func)
    await session.flush()


# --- Function Runs (D1) ---


async def create_run(
    session: AsyncSession,
    *,
    function_id: int,
    trigger: str,
    input: dict | None = None,
    context_type: str | None = None,
    context_id: str | None = None,
    dedup_key: str | None = None,
    created_by: str,
) -> FunctionRun:
    """Enqueue a new function run (status=queued)."""
    run = FunctionRun(
        function_id=function_id,
        status=FunctionRunStatus.queued,
        trigger=trigger,
        input=input or {},
        context_type=context_type,
        context_id=context_id,
        dedup_key=dedup_key,
        created_by=created_by,
    )
    session.add(run)
    await session.flush()
    # Update denormalised counter
    func = await session.get(Function, function_id)
    if func:
        func.run_count += 1
        session.add(func)
    return run


async def list_runs(
    session: AsyncSession,
    function_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[FunctionRun], int]:
    base = select(FunctionRun).where(FunctionRun.function_id == function_id)
    return await paginate(
        session, base, FunctionRun.created_at.desc(), skip=skip, limit=limit
    )


async def get_run(
    session: AsyncSession, run_id: uuid.UUID
) -> FunctionRun | None:
    return await session.get(FunctionRun, run_id)


async def update_run_status(
    session: AsyncSession,
    run: FunctionRun,
    status: FunctionRunStatus,
    *,
    output: dict | None = None,
    error: str | None = None,
) -> FunctionRun:
    """Transition a run to a new status (D1)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    run.status = status
    if status == FunctionRunStatus.running and run.started_at is None:
        run.started_at = now
    if status in (FunctionRunStatus.success, FunctionRunStatus.failure, FunctionRunStatus.timeout, FunctionRunStatus.cancelled):
        run.ended_at = now
        if run.started_at:
            run.duration_ms = int((run.ended_at - run.started_at).total_seconds() * 1000)
    if output is not None:
        run.output = output
    if error is not None:
        run.error = error
    session.add(run)
    await session.flush()

    # Update denormalised counters on failure
    if status == FunctionRunStatus.failure:
        func = await session.get(Function, run.function_id)
        if func:
            func.error_count += 1
            session.add(func)
    return run


async def toggle_function(
    session: AsyncSession,
    func: Function,
    enabled: bool,
    updated_by: str,
) -> Function:
    func.enabled = enabled
    func.updated_at = datetime.now(UTC).replace(tzinfo=None)
    func.updated_by = updated_by
    session.add(func)
    await session.flush()
    return func
