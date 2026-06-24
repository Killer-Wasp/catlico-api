from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.function import (
    Function,
    FunctionCreate,
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
    func.updated_at = datetime.now(UTC)
    func.updated_by = updated_by
    session.add(func)
    await session.flush()
    return func


async def delete_function(
    session: AsyncSession, func: Function, deleted_by: str
) -> None:
    func.deleted_at = datetime.now(UTC)
    func.deleted_by = deleted_by
    session.add(func)
    await session.flush()
