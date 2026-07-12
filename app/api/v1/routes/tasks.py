from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import flag as flag_crud
from app.crud import log as log_crud
from app.crud import organisation_member as member_crud
from app.crud import task as task_crud
from app.crud.case_share import get_share
from app.models.case_ import Case
from app.models.common import Page
from app.models.flag import FlagEntityType
from app.models.log import LogCreate, LogPublic
from app.models.role import RolePermission, expand_permissions
from app.models.task import (
    TASK_STATUS_TRANSITIONS,
    Task,
    TaskPublic,
    TaskQueueFacets,
    TaskQueuePublic,
    TaskUpdate,
)
from app.models.task_share import TaskShare
from app.models.user import User

# Item-level task ops are nested under the case so case_id is structural — the
# composite key (case_id, id) is read straight from the path, never parsed.
router = APIRouter(prefix="/cases/{case_id}/tasks", tags=["tasks"])
# The cross-case task queue spans cases, so it can't live under /cases/{case_id}.
queue_router = APIRouter(prefix="/task-queue", tags=["tasks"])


def _task_public(task: Task, flagged: bool) -> TaskPublic:
    pub = TaskPublic.model_validate(task, from_attributes=True)
    pub.flagged = flagged
    return pub


def _task_queue_public(
    task: Task,
    *,
    flagged: bool,
    case: Case,
    assignee_email: str | None,
) -> TaskQueuePublic:
    return TaskQueuePublic(
        **_task_public(task, flagged).model_dump(),
        case_title=case.title,
        case_severity=case.severity,
        assignee_email=assignee_email,
    )


async def _resolve_task_visibility(
    session: AsyncSession,
    ctx: ActiveOrgOrApiKeyContext,
    case_id: int,
    task_id: int,
) -> tuple[Task, bool, set[str]]:
    """Return (task, is_owner, effective_permissions) for the active org.
    Raises 404 if the active org cannot see the task (or it is soft-deleted).
    Permissions are intersection of org-role and case_share-role."""
    task = await task_crud.get_task(session, case_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if ctx.user.is_superadmin:
        return task, True, ctx.permissions

    share = await get_share(session, task.case_id, ctx.organisation_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if not share.is_owner:
        task_share_row = await session.get(
            TaskShare, (task.case_id, task.id, ctx.organisation_id)
        )
        if task_share_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

    pinned = await session.execute(
        select(RolePermission.permission).where(
            RolePermission.role_id == share.role_id
        )
    )
    effective = ctx.permissions & expand_permissions(pinned.scalars().all())
    return task, share.is_owner, effective


def _require(perm: str, perms: set[str]) -> None:
    if perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


@queue_router.get("", response_model=Page[TaskQueuePublic])
async def list_task_queue(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    filter: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "caseId",
    order: Annotated[str, Query()] = "asc",
) -> Page[TaskQueuePublic]:
    """Cross-case task queue: every task the active org may see across all its cases
    (owned + shared via task_share), enriched with case + assignee context. Each
    `filter` term is `key~op~value` (keys: status, assignee, kind, case, title),
    OR-within-key / AND-across-key. Read-only aggregate — item mutations go through
    the nested /cases/{case_id}/tasks routes."""
    _require("read:task", ctx.permissions)
    filters = task_crud.TaskListFilter.from_query(filter, sort=sort, order=order)
    tasks, total = await task_crud.list_tasks_for_org(
        session,
        organisation_id=ctx.organisation_id,
        skip=skip,
        limit=limit,
        filters=filters,
    )

    flagged = await flag_crud.flagged_ids(
        session, FlagEntityType.task, [t.public_id for t in tasks], ctx.organisation_id
    )
    case_ids = {task.case_id for task in tasks}
    case_rows = (
        (await session.execute(select(Case).where(Case.id.in_(case_ids))))
        .scalars()
        .all()
        if case_ids
        else []
    )
    cases_by_id = {case.id: case for case in case_rows}

    assignee_ids = {task.assignee_id for task in tasks if task.assignee_id is not None}
    user_rows = (
        (await session.execute(select(User).where(User.id.in_(assignee_ids))))
        .scalars()
        .all()
        if assignee_ids
        else []
    )
    users_by_id = {user.id: user for user in user_rows}

    return Page(
        items=[
            _task_queue_public(
                task,
                flagged=task.public_id in flagged,
                case=cases_by_id[task.case_id],
                assignee_email=(
                    users_by_id[task.assignee_id].email
                    if task.assignee_id in users_by_id
                    else None
                ),
            )
            for task in tasks
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@queue_router.get("/filters", response_model=TaskQueueFacets)
async def list_task_queue_filters(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskQueueFacets:
    """Distinct assignee/kind values across the org's task queue, for the list
    view's filter dropdowns."""
    _require("read:task", ctx.permissions)
    return await task_crud.task_queue_facets(session, ctx.organisation_id)


@router.get("/{task_id}", response_model=TaskPublic)
async def get_task(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskPublic:
    task, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.task, task.public_id, ctx.organisation_id
    )
    return _task_public(task, flagged)


@router.patch("/{task_id}", response_model=TaskPublic)
async def update_task(
    case_id: int,
    task_id: int,
    task_in: TaskUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskPublic:
    task, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("write:task", perms)

    # Enforce the status state machine (improvement over TheHive4's any->any).
    if task_in.status is not None and task_in.status != task.status:
        allowed = TASK_STATUS_TRANSITIONS.get(task.status, set())
        if task_in.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Illegal status transition {task.status.value} -> "
                    f"{task_in.status.value}"
                ),
            )

    if task_in.assignee_id:
        # Assignee must be a member of the task's creator org — a task is that org's
        # work item, regardless of which org is editing it.
        if not await member_crud.get_member(
            session, task_in.assignee_id, task.organisation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assignee must be a member of the task's organisation",
            )
    task = await task_crud.update_task(session, task, task_in, updated_by=str(ctx.user.id))
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.task, task.public_id, ctx.organisation_id
    )
    return _task_public(task, flagged)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, is_owner, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("delete:task", perms)
    # Creator's org OR owner org can delete.
    if not (is_owner or task.organisation_id == ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator org or case owner can delete this task",
        )
    await task_crud.delete_task(session, task, deleted_by=str(ctx.user.id))


# --- Per-org flag ---

@router.put("/{task_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def flag_task(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    await flag_crud.set_flag(
        session,
        FlagEntityType.task,
        task.public_id,
        ctx.organisation_id,
        created_by=str(ctx.user.id),
    )


@router.delete("/{task_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def unflag_task(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    await flag_crud.unset_flag(
        session, FlagEntityType.task, task.public_id, ctx.organisation_id
    )


# --- Logs under a task ---

@router.get("/{task_id}/logs", response_model=Page[LogPublic])
async def list_task_logs(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[LogPublic]:
    _, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    logs, total = await log_crud.list_logs_for_task(
        session, case_id, task_id, skip=skip, limit=limit
    )
    return Page(items=logs, total=total, skip=skip, limit=limit)


@router.post(
    "/{task_id}/logs",
    response_model=LogPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_log(
    case_id: int,
    task_id: int,
    log_in: LogCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    _, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("write:task", perms)
    return await log_crud.create_log(
        session,
        log_in,
        case_id=case_id,
        task_id=task_id,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
