import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import flag as flag_crud
from app.crud import log as log_crud
from app.crud import organisation_member as member_crud
from app.crud import task as task_crud
from app.crud.case_share import get_share
from app.models.common import Page
from app.models.flag import FlagEntityType
from app.models.log import LogCreate, LogPublic
from app.models.role import RolePermission
from app.models.task import (
    TASK_STATUS_TRANSITIONS,
    Task,
    TaskPublic,
    TaskUpdate,
)
from app.models.task_share import TaskShare

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_public(task: Task, flagged: bool) -> TaskPublic:
    pub = TaskPublic.model_validate(task, from_attributes=True)
    pub.flagged = flagged
    return pub


async def _resolve_task_visibility(
    session: AsyncSession,
    ctx: ActiveOrgContext,
    task_id: uuid.UUID,
) -> tuple[Task, bool, set[str]]:
    """Return (task, is_owner, effective_permissions) for the active org.
    Raises 404 if the active org cannot see the task (or it is soft-deleted).
    Permissions are intersection of org-role and case_share-role."""
    task = await session.get(Task, task_id)
    if not task or task.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if ctx.user.is_superadmin:
        return task, True, ctx.permissions

    share = await get_share(session, task.case_id, ctx.organisation_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if not share.is_owner:
        task_share_row = await session.get(
            TaskShare, (task_id, ctx.organisation_id)
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
    effective = ctx.permissions & set(pinned.scalars().all())
    return task, share.is_owner, effective


def _require(perm: str, perms: set[str]) -> None:
    if perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


@router.get("/{task_id}", response_model=TaskPublic)
async def get_task(
    task_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskPublic:
    task, _, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("read:task", perms)
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.task, str(task.id), ctx.organisation_id
    )
    return _task_public(task, flagged)


@router.patch("/{task_id}", response_model=TaskPublic)
async def update_task(
    task_id: uuid.UUID,
    task_in: TaskUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskPublic:
    task, _, perms = await _resolve_task_visibility(session, ctx, task_id)
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
        session, FlagEntityType.task, str(task.id), ctx.organisation_id
    )
    return _task_public(task, flagged)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, is_owner, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("write:task", perms)
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
    task_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, _, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("read:task", perms)
    await flag_crud.set_flag(
        session,
        FlagEntityType.task,
        str(task.id),
        ctx.organisation_id,
        created_by=str(ctx.user.id),
    )


@router.delete("/{task_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def unflag_task(
    task_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    task, _, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("read:task", perms)
    await flag_crud.unset_flag(
        session, FlagEntityType.task, str(task.id), ctx.organisation_id
    )


# --- Logs under a task ---

@router.get("/{task_id}/logs", response_model=Page[LogPublic])
async def list_task_logs(
    task_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[LogPublic]:
    _, _, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("read:task", perms)
    logs, total = await log_crud.list_logs_for_task(session, task_id, skip=skip, limit=limit)
    return Page(items=logs, total=total, skip=skip, limit=limit)


@router.post(
    "/{task_id}/logs",
    response_model=LogPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_log(
    task_id: uuid.UUID,
    log_in: LogCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    _, _, perms = await _resolve_task_visibility(session, ctx, task_id)
    _require("write:task", perms)
    return await log_crud.create_log(
        session,
        log_in,
        task_id=task_id,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
