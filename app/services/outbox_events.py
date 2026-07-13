"""Stable audit event envelope for outbox consumers.

Consumers receive AuditOutbox rows via the drain loop. This module provides the
canonical event shape so every consumer (notification feed, notifier dispatch,
WebSocket broadcast) sees the same envelope.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import notification as notif_crud
from app.models.audit import AuditOutbox
from app.models.notification import UserNotificationPublic

logger = logging.getLogger(__name__)

# ponytail: simple heuristics for notification titles — upgrade to config-driven
# templates when the product needs polish
_DEFAULT_TITLE = "Activity"
_PLUGIN_EVENT_ACTIONS = {
    "create": "created",
    "update": "updated",
    "delete": "deleted",
    "merge": "merged",
    "promote": "promoted",
    "restore": "restored",
}


def normalize_plugin_event_type(object_type: str, action: str) -> str:
    """Convert audit action vocabulary into plugin trigger vocabulary."""
    normalized_action = _PLUGIN_EVENT_ACTIONS.get(action, action)
    return f"{object_type}.{normalized_action}"


def build_event_envelope(row: AuditOutbox) -> dict[str, Any]:
    """Produce a stable JSON-serialisable event from an outbox row.

    Event type convention: ``<object_type>.<past-tense action>`` (e.g.
    ``case.created``).
    """
    payload: dict[str, Any] = row.payload or {}
    event_type = normalize_plugin_event_type(
        payload.get("object_type", "unknown"),
        payload.get("action", "unknown"),
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
    Notification title is derived from the event type (e.g. "case.created" →
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
    org_id = row.payload.get("organisation_id")
    if not org_id:
        # Org-agnostic event (global user/account mutation) — the in-app feed is
        # org-scoped, so there is nothing to notify. Mirrors the guard in
        # notifier_delivery_consumer and ws_broadcast_consumer.
        return
    await notif_crud.create_notification(
        session,
        organisation_id=org_id,
        user_id=None,
        event_type=envelope["event_type"],
        title=title,
        body=body,
        payload=envelope,
        outbox_id=row.id,
    )

    # Per-user routing: a (re)assignment of a case/task also gets a targeted
    # notification for the assignee, pushed live over the WS hub. The org-wide
    # row above is unchanged; this is additive.
    if obj_type in {"case", "task"}:
        target_user_id = _assignee_target(envelope.get("details") or {})
        if target_user_id:
            object_id = envelope["object"]["id"]
            await _notify_assignee(
                session,
                org_id=org_id,
                target_user_id=target_user_id,
                envelope=envelope,
                obj_type=obj_type,
                object_id=object_id,
                outbox_id=row.id,
            )


def _assignee_target(details: dict[str, Any]) -> str | None:
    """Return the newly-assigned user id from an audit `details` dict, or None.

    Two shapes are recognised:
    - update: ``assignee_id == [old, new]`` (the audit `{field: [old, new]}`
      diff form) → target ``new`` when it is truthy.
    - create-with-assignee: a bare ``assignee_id`` string → target it.
    """
    value = details.get("assignee_id")
    if isinstance(value, list):
        if len(value) == 2 and value[1]:
            return str(value[1])
        return None
    if isinstance(value, str) and value:
        return value
    return None


async def _notify_assignee(
    session: AsyncSession,
    *,
    org_id: str,
    target_user_id: str,
    envelope: dict[str, Any],
    obj_type: str,
    object_id: str,
    outbox_id: int | None = None,
) -> None:
    """Create the assignee's targeted notification (source of truth) and best-effort
    push it over the WS hub. A DB error propagates (drain retries); a WS send error
    is swallowed so a transient hub failure can't undo the notification."""
    try:
        user_uuid = uuid.UUID(target_user_id)
    except (ValueError, AttributeError, TypeError):
        # Malformed details value — skip the targeted notification rather than 500
        # the drain. The org-wide notification already landed.
        logger.warning("assignment routing: invalid assignee id %r", target_user_id)
        return

    notif = await notif_crud.create_notification(
        session,
        organisation_id=org_id,
        user_id=user_uuid,
        event_type=envelope["event_type"],
        title=f"You were assigned {obj_type} {object_id}",
        body=envelope.get("details", {}).get("summary", ""),
        payload=envelope,
        outbox_id=outbox_id,
    )
    if notif is None:
        return  # drain retry — the row (and its original push) already happened

    # Best-effort live push. Local import to avoid an import cycle (matches
    # ws_broadcast_consumer). A hub/send failure must not raise: the notification
    # row is the source of truth and the drain must not be marked undelivered.
    try:
        from app.services.websocket_hub import get_hub

        message = {
            "type": "notification",
            "notification": UserNotificationPublic.model_validate(notif).model_dump(
                mode="json"
            ),
        }
        await get_hub().send_to_user(org_id, target_user_id, message)
    except Exception:
        logger.exception("assignment routing: WS push failed (notification persisted)")
