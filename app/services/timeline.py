"""G1: Timeline projection service — merge audit, case, task, and observable
events into a chronological timeline for a case."""

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.audit import Audit
from app.models.case_ import Case
from app.models.task import Task
from app.models.observable import Observable


async def build_timeline(
    session: AsyncSession,
    case_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> list[dict]:
    """Return a chronologically-ordered list of events for a case.

    Includes: case creation, task creation/completion, observable creation,
    and all audited mutations. Merged case events are included via audit
    context links.
    """
    cid = str(case_id)
    events: list[dict] = []

    # Case events
    case = await session.get(Case, case_id)
    if case:
        events.append(_event("case", case.id, "created", case.created_at,
                             actor=case.created_by, details={"title": case.title}))

    # Task events
    result = await session.execute(
        select(Task).where(Task.case_id == case_id).order_by(Task.created_at)
    )
    for t in result.scalars().all():
        events.append(_event("task", t.public_id or t.id, "created", t.created_at,
                             actor=t.created_by, details={"title": t.title}))
        if t.status == "Completed" and t.updated_at and t.updated_at != t.created_at:
            events.append(_event("task", t.public_id or t.id, "completed", t.updated_at,
                                 actor=t.updated_by or "", details={"title": t.title}))

    # Observable events
    result = await session.execute(
        select(Observable).where(
            or_(Observable.case_id == case_id, Observable.alert_id == case_id)
        ).order_by(Observable.created_at)
    )
    for o in result.scalars().all():
        events.append(_event("observable", str(o.id), "created", o.created_at,
                             details={"type": o.observable_type, "data": o.data}))

    # Audit events
    cond = or_(
        (Audit.object_type == "case") & (Audit.object_id == cid),
        (Audit.context_type == "case") & (Audit.context_id == cid),
    )
    result = await session.execute(
        select(Audit).where(cond).order_by(Audit.created_at).offset(skip).limit(limit)
    )
    for a in result.scalars().all():
        events.append(_event(
            a.object_type, a.object_id, a.action, a.created_at,
            actor=a.actor, details=a.details,
        ))

    events.sort(key=lambda e: e["timestamp"])
    return events[skip:skip + limit] if skip else events[:limit]


def _event(
    entity_type: str, entity_id, action: str, timestamp: datetime,
    *, actor: str = "system", details: dict | None = None,
) -> dict:
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "action": action,
        "actor": actor,
        "timestamp": timestamp.isoformat() if timestamp else "",
        "details": details or {},
    }
