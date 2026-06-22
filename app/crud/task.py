import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.crud.case_share import list_non_owner_org_ids
from app.crud.organisation_link import get_link
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.log import Log
from app.models.organisation_link import AutoShareMode
from app.models.task import (
    TASK_TERMINAL_STATUSES,
    Task,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from app.models.task_share import TaskShare


def _public_id_sequence(public_id: str | None, case_id: int) -> int | None:
    prefix = f"T-{case_id}-"
    if public_id is None or not public_id.startswith(prefix):
        return None
    sequence = public_id.removeprefix(prefix)
    if not sequence.isdecimal():
        return None
    return int(sequence)


async def allocate_task_public_ids(
    session: AsyncSession, case_id: int, *, count: int = 1
) -> list[str]:
    if count < 1:
        return []

    await session.execute(select(Case.id).where(Case.id == case_id).with_for_update())
    result = await session.execute(select(Task.public_id).where(Task.case_id == case_id))
    max_sequence = max(
        (
            sequence
            for public_id in result.scalars().all()
            if (sequence := _public_id_sequence(public_id, case_id)) is not None
        ),
        default=0,
    )

    return [
        f"T-{case_id}-{sequence}"
        for sequence in range(max_sequence + 1, max_sequence + count + 1)
    ]


async def summaries_for_cases(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, list[Task]]:
    """Bulk: non-deleted tasks grouped by case_id. One query for a page of
    cases — the list endpoint embeds these so clients derive their own
    done/total without an extra round-trip per case."""
    if not case_ids:
        return {}
    result = await session.execute(
        select(Task)
        .where(Task.case_id.in_(case_ids), Task.deleted_at.is_(None))
        .order_by(Task.case_id, Task.group, Task.order, Task.created_at)
    )
    out: dict[int, list[Task]] = {}
    for task in result.scalars().all():
        out.setdefault(task.case_id, []).append(task)
    return out


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    """Returns the task only if it exists and is not soft-deleted."""
    task = await session.get(Task, task_id)
    if task is None or task.deleted_at is not None:
        return None
    return task


async def list_tasks_for_case(
    session: AsyncSession,
    case_id: int,
    *,
    organisation_id: str,
    is_owner: bool,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Task], int]:
    base = select(Task).where(Task.case_id == case_id, Task.deleted_at.is_(None))
    if not is_owner:
        base = base.join(TaskShare, TaskShare.task_id == Task.id).where(
            TaskShare.organisation_id == organisation_id
        )

    # Order per-group so `order` is scoped within a group, not globally across the case.
    return await paginate(
        session, base, Task.group, Task.order, Task.created_at, skip=skip, limit=limit
    )


async def list_tasks_for_org(
    session: AsyncSession,
    *,
    organisation_id: str,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Task], int]:
    """Every live task the active org may see, across all its cases: tasks on
    cases it owns (owner sees all) plus tasks explicitly shared to it via
    TaskShare. Same visibility rule as `list_tasks_for_case`, generalised across
    cases. Newest first."""
    owner_case_ids = select(CaseShare.case_id).where(
        CaseShare.organisation_id == organisation_id,
        CaseShare.is_owner == True,  # noqa: E712
    )
    shared_task_ids = select(TaskShare.task_id).where(
        TaskShare.organisation_id == organisation_id
    )
    base = select(Task).where(
        Task.deleted_at.is_(None),
        or_(
            Task.case_id.in_(owner_case_ids),
            Task.id.in_(shared_task_ids),
        ),
    )
    return await paginate(session, base, Task.created_at.desc(), skip=skip, limit=limit)


async def list_groups_for_case(
    session: AsyncSession,
    case_id: int,
    *,
    organisation_id: str,
    is_owner: bool,
) -> list[str]:
    """Distinct non-empty task group names visible to the caller's org — for UI autocomplete."""
    base = (
        select(Task.group)
        .where(
            Task.case_id == case_id,
            Task.deleted_at.is_(None),
            Task.group != "",
        )
        .distinct()
    )
    if not is_owner:
        base = base.join(TaskShare, TaskShare.task_id == Task.id).where(
            TaskShare.organisation_id == organisation_id
        )
    result = await session.execute(base.order_by(Task.group))
    return list(result.scalars().all())


async def create_task(
    session: AsyncSession,
    task_in: TaskCreate,
    *,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> Task:
    task = Task(
        public_id=(await allocate_task_public_ids(session, case_id))[0],
        case_id=case_id,
        organisation_id=organisation_id,
        title=task_in.title,
        group=task_in.group,
        description=task_in.description,
        assignee_id=task_in.assignee_id,
        order=task_in.order,
        start_date=task_in.start_date,
        due_date=task_in.due_date,
        created_by=created_by,
    )
    session.add(task)
    await session.flush()

    # Auto-share fan-out: for each non-owner org on the case, look up the directed
    # organisation_link from creator-org → target-org. If task_sharing=autoShare,
    # create a task_share row in the same transaction.
    target_org_ids = await list_non_owner_org_ids(session, case_id)
    # Also include the case-owner orgs that are NOT the creator org (owner sees by default,
    # but if creator is a non-owner, owner needs explicit share to see).
    owner_orgs = await session.execute(
        select(CaseShare.organisation_id).where(
            CaseShare.case_id == case_id,
            CaseShare.is_owner == True,  # noqa: E712
        )
    )
    candidate_orgs = set(target_org_ids) | set(owner_orgs.scalars().all())
    candidate_orgs.discard(organisation_id)

    for target_org_id in candidate_orgs:
        link = await get_link(session, organisation_id, target_org_id)
        if link is None or link.task_sharing != AutoShareMode.auto_share:
            continue
        session.add(
            TaskShare(
                task_id=task.id,
                organisation_id=target_org_id,
                created_by=created_by,
            )
        )

    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=task,
        context_type="case",
        context_id=str(case_id),
        actor=created_by,
        details={"title": task.title, "group": task.group},
    )
    return task


async def update_task(
    session: AsyncSession, task: Task, task_in: TaskUpdate, updated_by: str
) -> Task:
    update_data = task_in.model_dump(exclude_unset=True)

    # Auto-manage end_date on status transitions: set it when entering a terminal
    # state, clear it on re-open. Transition legality is validated in the handler.
    if "status" in update_data:
        new_status: TaskStatus = update_data["status"]
        if new_status in TASK_TERMINAL_STATUSES and task.status not in TASK_TERMINAL_STATUSES:
            update_data["end_date"] = datetime.now(UTC)
        elif new_status not in TASK_TERMINAL_STATUSES and task.status in TASK_TERMINAL_STATUSES:
            update_data["end_date"] = None

    changes = {
        field: [getattr(task, field, None), new]
        for field, new in update_data.items()
        if field not in ("updated_at", "updated_by")
        and getattr(task, field, None) != new
    }
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    task.sqlmodel_update(update_data)
    session.add(task)
    await session.flush()
    if changes:
        await record_audit(
            session,
            action="update",
            obj=task,
            context_type="case",
            context_id=str(task.case_id),
            actor=updated_by,
            details=changes,
        )
    return task


async def delete_task(session: AsyncSession, task: Task, deleted_by: str) -> None:
    """Soft delete: mark the task deleted and cascade the flag to its logs."""
    now = datetime.now(UTC)
    task.deleted_at = now
    task.deleted_by = deleted_by
    session.add(task)
    await session.execute(
        update(Log)
        .where(Log.task_id == task.id, Log.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.flush()
    await record_audit(
        session,
        action="delete",
        obj=task,
        context_type="case",
        context_id=str(task.case_id),
        actor=deleted_by,
    )
