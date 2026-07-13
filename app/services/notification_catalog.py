"""Curated catalog of notifiable in-app event types (A2).

Each entry is ``(event_type, label, category)``. The ``event_type`` strings match
the emitted ``<object_type>.<normalized_action>`` format produced by
``app.services.outbox_events`` (e.g. ``case.created``, ``case.merged``) — where
``object_type`` is the model ``__tablename__`` with a trailing ``_`` stripped.
Every entry here is verified to correspond to an audit event that the codebase
actually emits, so no mute toggle is silently dead. Entries under "Assignments"
and "Mentions" are the exception: their ``<obj>.assigned`` / ``<obj>.mentioned``
event types are *synthesised* by the feed consumer (`app.services.outbox_events`)
for the targeted assignee/mentioned-user row, not raw ``<object_type>.<action>``
audit actions.
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
    # Assignments (targeted; synthetic event types emitted by the feed consumer)
    ("case.assigned", "Case assigned to you", "Assignments"),
    ("task.assigned", "Task assigned to you", "Assignments"),
    ("alert.assigned", "Alert assigned to you", "Assignments"),
    # Mentions (targeted; synthetic event types emitted by the feed consumer)
    ("comment.mentioned", "Mentioned in a comment", "Mentions"),
    ("case.mentioned", "Mentioned in a case description", "Mentions"),
]


def catalog_event_types() -> set[str]:
    """The set of event_type strings in the catalog."""
    return {event_type for event_type, _label, _category in NOTIFICATION_EVENT_CATALOG}
