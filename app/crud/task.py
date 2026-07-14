from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, cast, func, or_, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._filters import (
    FilterClause,
    enum_condition,
    group_by_key,
    parse_clauses,
)
from app.crud._seq import next_task_ids
from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.crud.case_share import list_non_owner_org_ids
from app.crud.organisation_link import get_link
from app.models.case_share import CaseShare
from app.models.log import Log
from app.models.organisation_link import AutoShareMode
from app.models.task import (
    TASK_TERMINAL_STATUSES,
    Task,
    TaskCreate,
    TaskQueueFacets,
    TaskStatus,
    TaskUpdate,
)
from app.models.task_share import TaskShare
from app.models.user import User


#: Assignee sentinel meaning "no assignee". #: "General" is the UI kind for tasks
#: with no group.
_UNASSIGNED = "Unassigned"
_GENERAL_KIND = "General"

_TASK_SORT_COLUMNS = {
    "caseId": Task.case_id,
    "title": Task.title,
    "assignee": Task.assignee_id,
    "due": Task.due_date,
    "status": Task.status,
}


@dataclass(frozen=True)
class TaskListFilter:
    """Clause filter + sort for the task queue (OR-within-key / AND-across-key).
    Keys: status, assignee, kind, case, title."""

    clauses: tuple[FilterClause, ...] = ()
    sort: str = "caseId"
    order: str = "asc"

    @classmethod
    def from_query(
        cls, raw_filters: list[str] | None = None, *, sort="caseId", order="asc"
    ) -> "TaskListFilter":
        return cls(clauses=parse_clauses(raw_filters), sort=sort, order=order)


def _task_clause_cond(key: str, clause: FilterClause):
    v = clause.value
    contains = clause.contains
    if key == "status":
        # Native enum column: resolve values in Python (see enum_condition).
        return enum_condition(Task.status, TaskStatus, clause)
    if key == "assignee":
        if v == _UNASSIGNED:
            return Task.assignee_id.is_(None)
        email_cond = User.email.ilike(f"%{v}%") if contains else User.email == v
        return Task.assignee_id.in_(select(User.id).where(email_cond))
    if key == "kind":
        # "General" is the UI label for the empty group.
        if v == _GENERAL_KIND and not contains:
            return or_(Task.group == "", Task.group == _GENERAL_KIND)
        return Task.group.ilike(f"%{v}%") if contains else Task.group == v
    if key == "case":
        id_str = cast(Task.case_id, String)
        vv = v.lstrip("#")
        return id_str.ilike(f"%{vv}%") if contains else id_str == vv
    if key == "title":
        return Task.title.ilike(f"%{v}%") if contains else Task.title == v
    return None


def _apply_task_filters(stmt, f: TaskListFilter):
    for _key, clauses in group_by_key(f.clauses).items():
        conds = [
            c for c in (_task_clause_cond(_key, cl) for cl in clauses) if c is not None
        ]
        if conds:
            stmt = stmt.where(or_(*conds))
    return stmt


def _task_order_by(f: TaskListFilter):
    col = _TASK_SORT_COLUMNS.get(f.sort, Task.case_id)
    direction = col.asc() if f.order == "asc" else col.desc()
    return direction, Task.id.desc()


def _visible_task_condition(organisation_id: str):
    """Visibility predicate: tasks on cases the org owns, plus tasks shared to
    it via TaskShare. Shared by the queue list and its facets."""
    owner_case_ids = select(CaseShare.case_id).where(
        CaseShare.organisation_id == organisation_id,
        CaseShare.is_owner == True,  # noqa: E712
    )
    shared_task_keys = select(TaskShare.case_id, TaskShare.task_id).where(
        TaskShare.organisation_id == organisation_id
    )
    return or_(
        Task.case_id.in_(owner_case_ids),
        tuple_(Task.case_id, Task.id).in_(shared_task_keys),
    )


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


async def get_task(session: AsyncSession, case_id: int, id: int) -> Task | None:
    """Returns the task only if it exists and is not soft-deleted. Identity is the
    composite (case_id, id)."""
    task = await session.get(Task, (case_id, id))
    if task is None or task.deleted_at is not None:
        return None
    return task


#: Join predicate from TaskShare onto its task's composite key — reused wherever a
#: non-owner org's task visibility is resolved.
_SHARE_ON_TASK = (TaskShare.case_id == Task.case_id) & (TaskShare.task_id == Task.id)


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
        base = base.join(TaskShare, _SHARE_ON_TASK).where(
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
    filters: TaskListFilter | None = None,
) -> tuple[list[Task], int]:
    """Every live task the active org may see, across all its cases: tasks on
    cases it owns (owner sees all) plus tasks explicitly shared to it via
    TaskShare. Filtered, sorted and paginated server-side."""
    filters = filters or TaskListFilter()
    base = select(Task).where(
        Task.deleted_at.is_(None),
        _visible_task_condition(organisation_id),
    )
    base = _apply_task_filters(base, filters)
    return await paginate(
        session, base, *_task_order_by(filters), skip=skip, limit=limit
    )


async def task_queue_facets(
    session: AsyncSession, organisation_id: str
) -> TaskQueueFacets:
    """Distinct assignee/kind values across the org's visible tasks, plus whether
    any is unassigned — powers the queue's filter dropdowns across all pages."""
    visible = (
        select(Task.id, Task.assignee_id, Task.group)
        .where(Task.deleted_at.is_(None), _visible_task_condition(organisation_id))
        .subquery()
    )
    emails = list(
        (
            await session.execute(
                select(User.email)
                .join(visible, visible.c.assignee_id == User.id)
                .distinct()
                .order_by(User.email)
            )
        ).scalars()
    )
    unassigned = bool(
        (
            await session.execute(
                select(func.count())
                .select_from(visible)
                .where(visible.c.assignee_id.is_(None))
            )
        ).scalar_one()
    )
    groups = (
        (await session.execute(select(visible.c.group).distinct())).scalars().all()
    )
    kinds = sorted({g or _GENERAL_KIND for g in groups})
    return TaskQueueFacets(assignees=emails, unassigned=unassigned, kinds=kinds)


async def create_task(
    session: AsyncSession,
    task_in: TaskCreate,
    *,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> Task:
    (task_id,) = await next_task_ids(session, case_id)
    task = Task(
        id=task_id,
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
                case_id=task.case_id,
                task_id=task.id,
                organisation_id=target_org_id,
                created_by=created_by,
            )
        )

    await session.flush()
    details = {"title": task.title, "group": task.group}
    if task.assignee_id:
        # Stamp the create-time assignee so the feed consumer routes a targeted
        # `task.assigned` notification (matches the reassignment diff path).
        details["assignee_id"] = str(task.assignee_id)
    await record_audit(
        session,
        action="create",
        obj=task,
        context_type="case",
        context_id=str(case_id),
        actor=created_by,
        details=details,
        organisation_id=task.organisation_id,
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
            organisation_id=task.organisation_id,
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
        .where(
            Log.case_id == task.case_id,
            Log.task_id == task.id,
            Log.deleted_at.is_(None),
        )
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
        organisation_id=task.organisation_id,
    )
