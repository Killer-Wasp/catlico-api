"""Stable audit event envelope for outbox consumers.

Consumers receive AuditOutbox rows via the drain loop. This module provides the
canonical event shape so every consumer (notification feed, notifier dispatch,
WebSocket broadcast) sees the same envelope.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import notification as notif_crud
from app.models.audit import AuditOutbox

# ponytail: simple heuristics for notification titles — upgrade to config-driven
# templates when the product needs polish
_DEFAULT_TITLE = "Activity"


def build_event_envelope(row: AuditOutbox) -> dict[str, Any]:
    """Produce a stable JSON-serialisable event from an outbox row.

    Event type convention: ``<object_type>.<action>`` (e.g. ``case.create``).
    """
    payload: dict[str, Any] = row.payload or {}
    event_type = (
        f"{payload.get('object_type', 'unknown')}"
        f".{payload.get('action', 'unknown')}"
    )
    return {
        "event_id": f"audit:{row.audit_id}",
        "event_type": event_type,
        "actor": payload.get("actor", "system"),
        "object": {
            "type": payload.get("object_type", "unknown"),
            "id": payload.get("object_id", ""),
        },
        "context": {
            "type": payload.get("context_type", "unknown"),
            "id": payload.get("context_id", ""),
        },
        "details": payload.get("details") or {},
        "created_at": payload.get("created_at", ""),
    }


async def notify_feed_consumer(session: AsyncSession, row: AuditOutbox) -> None:
    """Outbox consumer: create a UserNotification row from every audit event.

    Registered via `register_consumer()` so it runs inside the drain transaction.
    Notification title is derived from the event type (e.g. "case.create" →
    "Case created").
    """
    envelope = build_event_envelope(row)
    obj_type = envelope["object"]["type"]
    action = envelope["event_type"].rsplit(".", 1)[-1]
    # ponytail: title generation fits the common case; add i18n/templates later
    title = f"{obj_type.title()} {action.replace('_', ' ')}"
    body = envelope.get("details", {}).get("summary", "")
    # ponytail: org-wide notifications (user_id=None) for now; per-user routing
    # when notification rules gain user-scoping
    org_id = row.payload.get("organisation_id", "")
    await notif_crud.create_notification(
        session,
        organisation_id=org_id,
        user_id=None,
        event_type=envelope["event_type"],
        title=title,
        body=body,
        payload=envelope,
    )
