import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.case_filters import (
    UNASSIGNED,
    CaseListFilter,
    case_list_facets,
    list_cases_for_org,
)
from app.crud.case_merge import MergeError, merge_cases
from app.models.case_ import (
    Case,
    CaseCreate,
    CaseUpdate,
)
from app.models.case_merge import CaseMerge
from app.models.case_share import CaseShare
from app.models.comment import Comment, CommentEntityType
from app.models.log import Log
from app.models.observable import Observable
from app.models.task import Task


async def get_case(session: AsyncSession, case_id: int) -> Case | None:
    """Returns the case only if it exists and is not soft-deleted."""
    case = await session.get(Case, case_id)
    if case is None or case.deleted_at is not None:
        return None
    return case


async def create_case(
    session: AsyncSession,
    case_in: CaseCreate,
    *,
    owner_org_id: str,
    owner_role_id: uuid.UUID,
    created_by: str,
) -> Case:
    case = Case(
        title=case_in.title,
        description=case_in.description,
        severity=case_in.severity,
        tlp=case_in.tlp,
        pap=case_in.pap,
        assignee_id=case_in.assignee_id,
        start_date=case_in.start_date,
        summary=case_in.summary,
        created_by=created_by,
    )
    session.add(case)
    await session.flush()
    share = CaseShare(
        case_id=case.id,
        organisation_id=owner_org_id,
        role_id=owner_role_id,
        is_owner=True,
        created_by=created_by,
    )
    session.add(share)
    await session.flush()
    details = {
        "title": case.title,
        "severity": case.severity,
        "tlp": case.tlp,
        "pap": case.pap,
    }
    if case.assignee_id:
        # Stamp the create-time assignee so the feed consumer routes a targeted
        # `case.assigned` notification (matches the reassignment diff path).
        details["assignee_id"] = str(case.assignee_id)
    await record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor=created_by,
        details=details,
        organisation_id=owner_org_id,
    )
    return case


async def update_case(
    session: AsyncSession,
    case: Case,
    case_in: CaseUpdate,
    updated_by: str,
    *,
    organisation_id: str,
) -> Case:
    update_data = case_in.model_dump(exclude_unset=True)
    changes = {
        field: [getattr(case, field, None), new]
        for field, new in update_data.items()
        if getattr(case, field, None) != new
    }
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    case.sqlmodel_update(update_data)
    session.add(case)
    await session.flush()
    if changes:
        await record_audit(
            session,
            action="update",
            obj=case,
            context=case,
            actor=updated_by,
            details=changes,
            organisation_id=organisation_id,
        )
    return case


async def delete_case(
    session: AsyncSession,
    case: Case,
    deleted_by: str,
    *,
    organisation_id: str,
) -> None:
    """Soft delete: flag the case and cascade the flag across the whole investigation
    — its tasks, those tasks' logs, its observables, and its comments. Mirrors
    delete_task's task->log cascade, widened to the case scope. CaseShare rows are
    left intact; reads exclude the case via its deleted_at, so they never surface it."""
    now = datetime.now(UTC)
    case.deleted_at = now
    case.deleted_by = deleted_by
    session.add(case)

    await session.execute(
        update(Task)
        .where(Task.case_id == case.id, Task.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Log)
        .where(Log.case_id == case.id, Log.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Observable)
        .where(Observable.case_id == case.id, Observable.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Comment)
        .where(
            Comment.entity_type == CommentEntityType.case,
            Comment.entity_id == str(case.id),
            Comment.deleted_at.is_(None),
        )
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.flush()
    await record_audit(
        session,
        action="delete",
        obj=case,
        context=case,
        actor=deleted_by,
        organisation_id=organisation_id,
    )


async def lineage_for_many(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, tuple[int | None, list[int]]]:
    """Batched merge lineage for a page of cases: {case_id: (merged_into, merged_from)}.

    One query over case_merge, never per-row — most pages have no merged cases and
    this returns nothing. `merged_into` = the successor this case was merged into;
    `merged_from` = the source cases this (new) case absorbed."""
    if not case_ids:
        return {}
    rows = (
        await session.execute(
            select(CaseMerge.source_case_id, CaseMerge.target_case_id).where(
                CaseMerge.source_case_id.in_(case_ids)
                | CaseMerge.target_case_id.in_(case_ids)
            )
        )
    ).all()
    wanted = set(case_ids)
    out: dict[int, tuple[int | None, list[int]]] = {cid: (None, []) for cid in case_ids}
    for source_id, target_id in rows:
        if source_id in wanted:
            into, frm = out[source_id]
            out[source_id] = (target_id, frm)
        if target_id in wanted:
            into, frm = out[target_id]
            out[target_id] = (into, [*frm, source_id])
    return out
