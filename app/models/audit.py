from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class Audit(SQLModel, table=True):
    """One row per top-level mutation. Targets are polymorphic strings
    (`object_type` from the model's tablename, `object_id = str(obj.id)`) — no FK,
    so the row survives a hard-delete of its target and sidesteps the mixed
    int/uuid PK problem, matching the Comment/Flag/Tag idiom. `context` points at
    the owning case for case-scoped children, which powers the activity feed."""

    __tablename__ = "audit"
    __table_args__ = (
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_context", "context_type", "context_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    request_id: str = Field(index=True)
    action: str  # create | update | delete | merge
    main_action: bool = True
    object_type: str
    object_id: str
    context_type: str | None = None
    context_id: str | None = None
    actor: str  # str(user.id) | "system" | "analyzer" — not a FK
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditOutbox(SQLModel, table=True):
    """Durable, post-commit fan-out queue. One row per audit in v1 (topic="audit").
    A poller drains undelivered rows after commit; consumers (stream/notification/
    connector) register at runtime — empty in v1."""

    __tablename__ = "audit_outbox"
    __table_args__ = (
        UniqueConstraint("audit_id", "topic", name="uq_audit_outbox_audit_topic"),
        # Partial index backing the platform-wide dead-letter COUNT in
        # crud/overview.py (_dead_letter_count), which runs on every overview build.
        Index(
            "ix_audit_outbox_dead_lettered_at",
            "dead_lettered_at",
            postgresql_where=text("dead_lettered_at IS NOT NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    audit_id: int = Field(foreign_key="audit.id", ondelete="CASCADE", index=True)
    topic: str = "audit"
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    delivered_at: datetime | None = None
    attempts: int = 0
    #: Terminal marker: set once `attempts` reaches `MAX_OUTBOX_ATTEMPTS`. A
    #: dead-lettered row is excluded from the drain (like a delivered one) so it
    #: stops retrying forever, and is pruned on its own longer retention window.
    dead_lettered_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuditPublic(SQLModel):
    id: int
    request_id: str
    action: str
    main_action: bool
    object_type: str
    object_id: str
    context_type: str | None
    context_id: str | None
    actor: str
    details: dict[str, Any] | None
    created_at: datetime
