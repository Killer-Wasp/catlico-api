"""Audit + outbox write path, drain, and the case activity read.

`record_audit` runs inside the request transaction (it only flushes; `get_session`
owns the commit) so a mutation and its audit + outbox rows land atomically. It is
total on `details` content — non-serialisable values are coerced to str rather than
raising — but a genuine DB-write failure propagates and rolls back the mutation
rather than silently dropping the trail.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_request_id
from app.models.audit import Audit, AuditOutbox

# Keys whose values must never be written to an audit row. Match is by exact key
# or by substring for the obviously-sensitive families. Audit rows are themselves
# a readable table and an outbox payload, so this is a security boundary.
_REDACT_EXACT = {"config", "settings", "manifest"}
_REDACT_SUBSTR = ("password", "secret", "token", "api_key", "apikey", "hashed_")
_REDACTED = "[redacted]"


def _type_of(obj: Any) -> str:
    """Canonical polymorphic type string for a model. Strips the trailing underscore
    that escapes the SQL keyword in `case_`, so the audit type is `case` — matching
    the existing Comment/Flag/Tag entity-type convention and the `context` values
    child mutations pass explicitly."""
    return obj.__tablename__.removesuffix("_")


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return k in _REDACT_EXACT or any(s in k for s in _REDACT_SUBSTR)


def _coerce(value: Any) -> Any:
    """JSON-safe coercion that never raises on content."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce(v) for v in value]
    return str(value)


def _redact(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if not details:
        return details
    return {
        k: (_REDACTED if _is_sensitive(k) else _coerce(v)) for k, v in details.items()
    }


# Outbox consumers: async fn(payload) -> None. Empty in v1 — the drain still runs
# end-to-end and marks rows delivered. Registering a consumer is the seam for real
# stream/notification/connector fan-out.
_consumers: list[Callable[[dict[str, Any]], Awaitable[None]]] = []


def register_consumer(fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    _consumers.append(fn)


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    obj: Any,
    actor: str,
    context: Any | None = None,
    context_type: str | None = None,
    context_id: str | None = None,
    details: dict[str, Any] | None = None,
    main_action: bool = True,
) -> Audit:
    """Write one audit row + one outbox row in the caller's transaction.

    `object_type` is derived from `obj.__tablename__`. Pass a `context` model
    (typically the owning Case) to scope the row for the activity feed, or pass
    `context_type`/`context_id` directly when the parent object isn't in hand.
    """
    if context is not None:
        context_type = _type_of(context)
        context_id = str(context.id)

    audit = Audit(
        request_id=get_request_id(),
        action=action,
        main_action=main_action,
        object_type=_type_of(obj),
        object_id=str(obj.id),
        context_type=context_type,
        context_id=context_id,
        actor=actor,
        details=_redact(details),
    )
    session.add(audit)
    await session.flush()  # assign audit.id for the outbox FK

    session.add(
        AuditOutbox(
            audit_id=audit.id,
            topic="audit",
            payload={
                "request_id": audit.request_id,
                "action": audit.action,
                "object_type": audit.object_type,
                "object_id": audit.object_id,
                "context_type": audit.context_type,
                "context_id": audit.context_id,
                "actor": audit.actor,
                "details": audit.details,
                "created_at": audit.created_at.isoformat(),
            },
        )
    )
    await session.flush()
    return audit


async def dispatch_pending_outbox(session: AsyncSession, *, limit: int = 100) -> int:
    """Drain undelivered outbox rows: hand each to every registered consumer, then
    mark delivered and bump attempts. Runs on its own session (the poller's), so it
    commits. Returns the number of rows processed."""
    rows = (
        (
            await session.execute(
                select(AuditOutbox)
                .where(AuditOutbox.delivered_at.is_(None))
                .order_by(AuditOutbox.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.attempts += 1
        for consumer in _consumers:
            await consumer(row.payload)
        row.delivered_at = datetime.now(UTC)
        session.add(row)
    if rows:
        await session.commit()
    return len(rows)


async def list_case_activity(
    session: AsyncSession, case_id: int, *, skip: int = 0, limit: int = 100
) -> tuple[list[Audit], int]:
    """The case's own changes plus all child activity scoped to it via `context`."""
    cid = str(case_id)
    cond = or_(
        (Audit.object_type == "case") & (Audit.object_id == cid),
        (Audit.context_type == "case") & (Audit.context_id == cid),
    )
    base = select(Audit).where(cond)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()
    rows = (
        (await session.execute(base.order_by(Audit.id.desc()).offset(skip).limit(limit)))
        .scalars()
        .all()
    )
    return list(rows), total
