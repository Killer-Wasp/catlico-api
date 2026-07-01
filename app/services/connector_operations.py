"""E1: Responder operation schema and application service.

Responders submit a list of operations (add_tag, create_task, etc.) alongside
their result. This module validates and applies them transactionally through
the existing CRUD/service layer, emitting audit rows for each mutation.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import comment as comment_crud
from app.crud import flag as flag_crud
from app.crud import organisation_member as member_crud
from app.crud import observable as obs_crud
from app.crud import tag as tag_crud
from app.crud import task as task_crud
from app.crud.connector import get as get_connector
from app.util.ids import parse_task_id


class OperationKind(str, Enum):
    add_tag = "add_tag"
    create_task = "create_task"
    add_comment = "add_comment"
    update_case = "update_case"
    update_observable = "update_observable"
    close_task = "close_task"
    assign_case = "assign_case"


class Operation(BaseModel):
    kind: OperationKind
    params: dict = {}


class ResponderResult(BaseModel):
    lease_token: uuid.UUID
    status: str  # "success" | "failure"
    operations: list[Operation] = []
    message: str = ""
    full: dict = {}
    error: str | None = None
    dry_run: bool = False  # E1+: return intended changes without applying
    require_confirmation: bool = False  # E1+: require explicit user confirmation


async def _validate_operation(
    session: AsyncSession,
    op: Operation,
    organisation_id: str,
    case_id: int | None,
) -> str | None:
    """Validate that the operation is allowed. Returns error message or None."""
    kind = op.kind
    params = op.params

    if kind == OperationKind.add_tag:
        tag_name = params.get("tag")
        if not tag_name:
            return "add_tag requires 'tag' param"
        # Validate tag exists in org or is a known namespace
        tag = await tag_crud.get_by_name(session, tag_name, organisation_id)
        if tag is None:
            return f"Tag '{tag_name}' not found in organisation"

    elif kind == OperationKind.create_task:
        if not case_id:
            return "create_task requires a case context"
        title = params.get("title")
        if not title:
            return "create_task requires 'title' param"

    elif kind == OperationKind.add_comment:
        if not case_id:
            return "add_comment requires a case context"
        if not params.get("message"):
            return "add_comment requires 'message' param"

    elif kind == OperationKind.close_task:
        task_id = params.get("task_id")
        if not task_id:
            return "close_task requires 'task_id' param"
        if not str(task_id).startswith("T-") and case_id is None:
            return "close_task requires a public task id or a case context"

    elif kind == OperationKind.assign_case:
        if not case_id:
            return "assign_case requires a case context"
        assignee_id = params.get("assignee_id")
        if not assignee_id:
            return "assign_case requires 'assignee_id' param"
        try:
            assignee_uuid = uuid.UUID(str(assignee_id))
        except ValueError:
            return "assign_case requires a valid assignee_id UUID"
        if await member_crud.get_member(session, assignee_uuid, organisation_id) is None:
            return "Assignee must be a member of the organisation"

    elif kind == OperationKind.update_observable:
        obs_id = params.get("observable_id")
        if not obs_id:
            return "update_observable requires 'observable_id' param"

    elif kind == OperationKind.update_case:
        if not case_id:
            return "update_case requires a case context"

    return None


async def apply_operations(
    session: AsyncSession,
    operations: list[Operation],
    *,
    organisation_id: str,
    connector_name: str,
    case_id: int | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Apply a list of operations transactionally (all-or-nothing for now).
    Returns list of error messages. Empty list = all succeeded.

    When `dry_run=True`, validates operations but does NOT apply them — returns
    the list of operations that *would* be applied (or validation errors).

    The actor for audit is set to `connector:<connector_name>`.
    """
    actor = f"connector:{connector_name}"
    errors: list[str] = []

    # Pre-validate all operations first (catch failures before any mutation)
    for op in operations:
        err = await _validate_operation(session, op, organisation_id, case_id)
        if err:
            errors.append(err)

    if errors:
        return errors

    if dry_run:
        # ponytail: dry-run returns what would happen without applying
        return []

    # Apply operations
    for op in operations:
        try:
            err = await _apply_one(session, op, organisation_id, actor, case_id)
            if err:
                errors.append(err)
        except Exception as exc:
            errors.append(f"{op.kind.value}: {exc}")

    if not errors:
        await session.flush()
    return errors


async def _apply_one(
    session: AsyncSession,
    op: Operation,
    organisation_id: str,
    actor: str,
    case_id: int | None,
) -> str | None:
    """Apply a single operation. Returns error string or None."""
    params = op.params

    if op.kind == OperationKind.add_tag:
        tag_name = params["tag"]
        if case_id:
            case = await case_crud.get_case(session, case_id)
            if case is None:
                return "Case not found"
            await flag_crud.attach_tag(session, case, tag_name, actor=actor)

    elif op.kind == OperationKind.create_task:
        from app.models.task import TaskCreate
        task_in = TaskCreate(
            title=params["title"],
            description=params.get("description", ""),
            group=params.get("group", ""),
        )
        # ponytail: inline creation; route-level creation is richer
        await task_crud.create_task(
            session,
            task_in,
            case_id=case_id,
            organisation_id=organisation_id,
            created_by=actor,
        )

    elif op.kind == OperationKind.add_comment:
        from app.models.comment import CommentCreate, CommentEntityType
        comment_in = CommentCreate(
            message=params["message"],
        )
        await comment_crud.create_comment(
            session,
            comment_in,
            entity_type=CommentEntityType.case,
            entity_id=str(case_id),
            organisation_id=organisation_id,
            created_by=actor,
        )

    elif op.kind == OperationKind.update_case:
        allowed = {k for k in params if k in ("title", "description", "severity", "status", "tlp", "pap")}
        if allowed:
            case = await case_crud.get_case(session, case_id)
            if case is None:
                return "Case not found"
            from app.models.case_ import CaseUpdate
            update = CaseUpdate(**{k: params[k] for k in allowed})
            await case_crud.update_case(session, case, update, updated_by=actor)

    elif op.kind == OperationKind.update_observable:
        obs_id = params.get("observable_id")
        if not obs_id:
            return "Missing observable_id"
        obs = await obs_crud.get_observable(session, uuid.UUID(obs_id))
        if obs is None:
            return f"Observable {obs_id} not found"
        allowed = {k for k in params if k in ("message", "tlp", "ioc", "sighted")}
        if allowed:
            from app.models.observable import ObservableUpdate
            update = ObservableUpdate(**{k: params[k] for k in allowed})
            await obs_crud.update_observable(session, obs, update, updated_by=actor)

    elif op.kind == OperationKind.close_task:
        task_id = params.get("task_id")
        task = None
        if task_id:
            try:
                close_case_id, close_task_id = parse_task_id(str(task_id))
            except ValueError:
                if case_id is None:
                    return f"Task '{task_id}' not found"
                try:
                    close_case_id = case_id
                    close_task_id = int(task_id)
                except (TypeError, ValueError):
                    return f"Task '{task_id}' not found"
            task = await task_crud.get_task(session, close_case_id, close_task_id)
        if task is None:
            return f"Task '{task_id}' not found"
        if task.organisation_id != organisation_id:
            return f"Task '{task_id}' not found"
        from app.models.task import TaskStatus, TaskUpdate

        await task_crud.update_task(
            session,
            task,
            TaskUpdate(status=TaskStatus.completed),
            updated_by=actor,
        )

    elif op.kind == OperationKind.assign_case:
        assignee_id = params.get("assignee_id")
        case = await case_crud.get_case(session, case_id)
        if case is None:
            return "Case not found"
        from app.models.case_ import CaseUpdate

        await case_crud.update_case(
            session,
            case,
            CaseUpdate(assignee_id=uuid.UUID(str(assignee_id))),
            updated_by=actor,
        )

    return None
