"""Multi-assignee (collaborator) join-table CRUD for cases + tasks.

The primary owner stays on ``Case.assignee_id`` / ``Task.assignee_id``; these
join tables (``case_assignee`` / ``task_assignee``) hold the additional
collaborators. The set operations return the *newly-added* collaborator ids so
the caller can stamp them into an audit event for targeted `.assigned`
notification fan-out. Alerts are single-assignee and have no equivalent here.
"""

import uuid

from sqlalchemy import and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import user as user_crud
from app.models.case_assignee import CaseAssignee
from app.models.common import AssigneeRef
from app.models.task_assignee import TaskAssignee


def assignee_refs(
    primary_id: uuid.UUID | None,
    collaborator_ids: list[uuid.UUID],
    emails: dict[uuid.UUID, str],
) -> list[AssigneeRef]:
    """Build the ordered assignee list (primary first, flagged) from already-fetched
    emails. Collaborators equal to the primary are dropped so the primary appears
    once. Pure — no DB access — so list paths can resolve all emails in one query."""
    refs: list[AssigneeRef] = []
    if primary_id is not None:
        refs.append(
            AssigneeRef(id=primary_id, email=emails.get(primary_id), is_primary=True)
        )
    for uid in collaborator_ids:
        if uid == primary_id:
            continue
        refs.append(AssigneeRef(id=uid, email=emails.get(uid), is_primary=False))
    return refs


async def build_assignee_refs(
    session: AsyncSession,
    *,
    primary_id: uuid.UUID | None,
    collaborator_ids: list[uuid.UUID],
) -> list[AssigneeRef]:
    """Single-entity convenience: resolve emails then build the ref list."""
    ids = list({*(collaborator_ids), *([primary_id] if primary_id else [])})
    emails = await user_crud.emails_for_ids(session, ids) if ids else {}
    return assignee_refs(primary_id, collaborator_ids, emails)


async def list_case_collaborators(
    session: AsyncSession, case_id: int
) -> list[uuid.UUID]:
    result = await session.execute(
        select(CaseAssignee.user_id).where(CaseAssignee.case_id == case_id)
    )
    return list(result.scalars().all())


async def collaborators_for_cases(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, list[uuid.UUID]]:
    """Bulk: collaborator user ids grouped by case_id (one query for a page)."""
    if not case_ids:
        return {}
    result = await session.execute(
        select(CaseAssignee.case_id, CaseAssignee.user_id).where(
            CaseAssignee.case_id.in_(case_ids)
        )
    )
    out: dict[int, list[uuid.UUID]] = {}
    for case_id, user_id in result.all():
        out.setdefault(case_id, []).append(user_id)
    return out


async def set_case_collaborators(
    session: AsyncSession,
    case_id: int,
    user_ids: list[uuid.UUID],
    *,
    primary_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Replace the collaborator set for a case. Returns the newly-added ids.

    The primary owner is never stored as a collaborator (it lives on
    ``assignee_id``), so it is filtered out here to keep the invariant
    "join rows == collaborators excluding the primary"."""
    desired = {uid for uid in user_ids if uid != primary_id}
    existing = set(await list_case_collaborators(session, case_id))
    to_add = desired - existing
    to_remove = existing - desired
    if to_remove:
        await session.execute(
            delete(CaseAssignee).where(
                and_(
                    CaseAssignee.case_id == case_id,
                    CaseAssignee.user_id.in_(to_remove),
                )
            )
        )
    for uid in to_add:
        session.add(CaseAssignee(case_id=case_id, user_id=uid))
    await session.flush()
    # Preserve caller order for the added ids (stable, testable).
    return [uid for uid in user_ids if uid in to_add]


async def list_task_collaborators(
    session: AsyncSession, case_id: int, task_id: int
) -> list[uuid.UUID]:
    result = await session.execute(
        select(TaskAssignee.user_id).where(
            TaskAssignee.case_id == case_id, TaskAssignee.task_id == task_id
        )
    )
    return list(result.scalars().all())


async def collaborators_for_tasks(
    session: AsyncSession, keys: list[tuple[int, int]]
) -> dict[tuple[int, int], list[uuid.UUID]]:
    """Bulk: collaborator user ids grouped by (case_id, task_id)."""
    if not keys:
        return {}
    case_ids = {case_id for case_id, _ in keys}
    wanted = set(keys)
    result = await session.execute(
        select(
            TaskAssignee.case_id, TaskAssignee.task_id, TaskAssignee.user_id
        ).where(TaskAssignee.case_id.in_(case_ids))
    )
    out: dict[tuple[int, int], list[uuid.UUID]] = {}
    for case_id, task_id, user_id in result.all():
        key = (case_id, task_id)
        if key in wanted:
            out.setdefault(key, []).append(user_id)
    return out


async def set_task_collaborators(
    session: AsyncSession,
    case_id: int,
    task_id: int,
    user_ids: list[uuid.UUID],
    *,
    primary_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Replace the collaborator set for a task. Returns the newly-added ids."""
    desired = {uid for uid in user_ids if uid != primary_id}
    existing = set(await list_task_collaborators(session, case_id, task_id))
    to_add = desired - existing
    to_remove = existing - desired
    if to_remove:
        await session.execute(
            delete(TaskAssignee).where(
                and_(
                    TaskAssignee.case_id == case_id,
                    TaskAssignee.task_id == task_id,
                    TaskAssignee.user_id.in_(to_remove),
                )
            )
        )
    for uid in to_add:
        session.add(TaskAssignee(case_id=case_id, task_id=task_id, user_id=uid))
    await session.flush()
    return [uid for uid in user_ids if uid in to_add]
