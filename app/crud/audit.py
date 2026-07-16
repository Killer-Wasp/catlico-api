"""Audit + outbox write path and drain.

`record_audit` runs inside the request transaction (it only flushes; `get_session`
owns the commit) so a mutation and its audit + outbox rows land atomically. It is
total on `details` content — non-serialisable values are coerced to str rather than
raising — but a genuine DB-write failure propagates and rolls back the mutation
rather than silently dropping the trail.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.core.context import get_request_id
from app.models.audit import Audit, AuditOutbox

logger = logging.getLogger(__name__)

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


def _object_id_of(obj: Any) -> str:
    value = (
        getattr(obj, "public_id", None)
        or getattr(obj, "id", None)
        or getattr(obj, "name", None)
    )
    return str(value)


def _display_title_of(obj: Any) -> str | None:
    """Best-effort human-readable name for an entity, used to enrich notification
    titles (Area 8). Cases/tasks/alerts carry `title`; observables carry `data`;
    catalog entities carry `name`. Returns None when nothing suitable is present so
    the consumer can fall back to the object id."""
    for attr in ("title", "name", "data"):
        value = getattr(obj, attr, None)
        if value:
            return str(value)
    return None


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


# Outbox consumers: receive the session + the outbox row so they can write
# notification/stream/connector rows in the same drain transaction. Registered at
# startup (`app.main.lifespan`): the in-app feed, external notifier delivery, the
# WebSocket broadcast, and plugin-event dispatch. Each runs in its own SAVEPOINT so
# one consumer's failure can't poison the others or the batch.
OutboxConsumer = Callable[[AsyncSession, AuditOutbox], Awaitable[None]]
_consumers: list[OutboxConsumer] = []


def register_consumer(fn: OutboxConsumer) -> None:
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
    organisation_id: str | None = None,
) -> Audit:
    """Write one audit row + one outbox row in the caller's transaction.

    `object_type` is derived from `obj.__tablename__`. Pass a `context` model
    (typically the owning Case) to scope the row for the activity feed, or pass
    `context_type`/`context_id` directly when the parent object isn't in hand.

    `organisation_id` (when truthy) is stamped onto the outbox payload and is what
    makes the event visible in the org-scoped `GET /api/v1/events` feed and the WS
    `/activity` stream (both filter on `payload->>'organisation_id'`). Omit it for
    genuinely org-agnostic events (global user/account and platform-wide catalog
    changes); pass the acting org for product mutations. Note the payload holds a
    single org, so for a case owned by one org but edited by a collaborating org the
    `update` event carries the *acting* org (delete is owner-gated, so it can't
    diverge).
    """
    context_title: str | None = None
    if context is not None:
        context_type = _type_of(context)
        context_id = str(context.id)
        context_title = _display_title_of(context)

    audit = Audit(
        request_id=get_request_id(),
        action=action,
        main_action=main_action,
        object_type=_type_of(obj),
        # Composite-keyed objects (task/log/attachment) expose a case-scoped
        # public_id (e.g. T-1234-1) that is globally unique and human-readable;
        # the bare integer `id` is only unique within its case. Fall back to id
        # for entities without one (case = its number, comment/observable = UUID).
        object_id=_object_id_of(obj),
        context_type=context_type,
        context_id=context_id,
        actor=actor,
        details=_redact(details),
    )
    session.add(audit)
    await session.flush()  # assign audit.id for the outbox FK

    payload: dict[str, Any] = {
        "request_id": audit.request_id,
        "action": audit.action,
        "object_type": audit.object_type,
        "object_id": audit.object_id,
        "context_type": audit.context_type,
        "context_id": audit.context_id,
        "actor": audit.actor,
        "details": audit.details,
        "created_at": audit.created_at.isoformat(),
    }
    # Enrichment (Area 8): stamp human-readable names into the outbox payload so the
    # notification consumer can build rich titles ("Task updated — <title>", with the
    # parent case name) without re-querying. These live only on the outbox payload,
    # not the audit row, and are never redacted (they are display names, not secrets).
    object_title = _display_title_of(obj)
    if object_title is not None:
        payload["object_title"] = object_title
    if context_title is not None:
        payload["context_title"] = context_title
    if organisation_id:
        payload["organisation_id"] = organisation_id

    session.add(
        AuditOutbox(
            audit_id=audit.id,
            topic="audit",
            payload=payload,
        )
    )
    await session.flush()
    return audit


async def dispatch_pending_outbox(session: AsyncSession, *, limit: int = 100) -> int:
    """Drain undelivered outbox rows: hand each to every registered consumer, then
    mark delivered only after *all* consumers complete successfully. Runs on its own
    session (the poller's), so it commits. Returns the number of rows delivered.

    A row that keeps failing is retried until `attempts` reaches
    `MAX_OUTBOX_ATTEMPTS`, at which point it is dead-lettered (a terminal
    `dead_lettered_at` marker) and excluded from future drains — the same way a
    delivered row is — so a poison row can't retry forever."""
    rows = (
        (
            await session.execute(
                select(AuditOutbox)
                .where(
                    AuditOutbox.delivered_at.is_(None),
                    AuditOutbox.dead_lettered_at.is_(None),
                )
                .order_by(AuditOutbox.id)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    delivered = 0
    for row in rows:
        row.attempts += 1
        session.add(row)
        all_succeeded = True
        for consumer in _consumers:
            try:
                async with session.begin_nested():
                    await consumer(session, row)
            except Exception:
                logger.exception(
                    "outbox consumer %s failed for outbox row %d",
                    getattr(consumer, "__name__", repr(consumer)),
                    row.id,
                )
                all_succeeded = False
        if all_succeeded:
            row.delivered_at = datetime.now(UTC)
            session.add(row)
            delivered += 1
        elif row.attempts >= settings.MAX_OUTBOX_ATTEMPTS:
            # Terminal: give up and dead-letter. Logged once here (the row is then
            # excluded from the drain query, so it never reaches this branch again).
            row.dead_lettered_at = datetime.now(UTC)
            session.add(row)
            logger.error(
                "outbox row %d dead-lettered after %d failed attempts",
                row.id,
                row.attempts,
            )
    if rows:
        await session.commit()
    return delivered
