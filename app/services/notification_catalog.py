"""Curated catalog of notifiable in-app event types (A2).

Each entry is ``(event_type, label, category)``. The ``event_type`` strings match
the emitted ``<object_type>.<normalized_action>`` format produced by
``app.services.outbox_events`` (e.g. ``case.created``, ``alert.promoted``). This
list drives the notification-preferences UI; being slightly over-inclusive is
fine because the read-time filter defaults every event to visible.
"""

from __future__ import annotations

# (event_type, label, category)
NOTIFICATION_EVENT_CATALOG: list[tuple[str, str, str]] = [
    # Cases
    ("case.created", "Case created", "Cases"),
    ("case.updated", "Case updated", "Cases"),
    ("case.deleted", "Case deleted", "Cases"),
    ("case.merged", "Case merged", "Cases"),
    # Alerts
    ("alert.created", "Alert created", "Alerts"),
    ("alert.updated", "Alert updated", "Alerts"),
    ("alert.deleted", "Alert deleted", "Alerts"),
    ("alert.promoted", "Alert promoted", "Alerts"),
    ("alert.merged", "Alert merged", "Alerts"),
    # Tasks
    ("task.created", "Task created", "Tasks"),
    ("task.updated", "Task updated", "Tasks"),
    ("task.deleted", "Task deleted", "Tasks"),
    # Observables
    ("observable.created", "Observable created", "Observables"),
    ("observable.updated", "Observable updated", "Observables"),
    ("observable.deleted", "Observable deleted", "Observables"),
    # Comments
    ("comment.created", "Comment added", "Comments"),
    # Case log entries
    ("log.created", "Case log entry added", "Case log"),
    # Knowledge base
    ("knowledge_base_page.created", "Knowledge base page created", "Knowledge base"),
    ("knowledge_base_page.updated", "Knowledge base page updated", "Knowledge base"),
]


def catalog_event_types() -> set[str]:
    """The set of event_type strings in the catalog."""
    return {event_type for event_type, _label, _category in NOTIFICATION_EVENT_CATALOG}
