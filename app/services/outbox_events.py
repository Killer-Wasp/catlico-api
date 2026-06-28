"""Stable audit event envelope for outbox consumers.

Consumers receive AuditOutbox rows via the drain loop. This module provides the
canonical event shape so every consumer (notification feed, notifier dispatch,
WebSocket broadcast) sees the same envelope.
"""

from __future__ import annotations

from typing import Any

from app.models.audit import AuditOutbox


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
