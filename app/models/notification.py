import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class NotifierType(str, Enum):
    slack = "slack"
    email = "email"
    webhook = "webhook"
    kafka = "kafka"


class Notifier(TimestampMixin, table=True):
    """An org-scoped delivery channel. `target` is the channel/address/url/topic
    (e.g. `#soc-alerts`); channel-specific extras live in `config`. Any secret
    (bot token, webhook signing secret) is encrypted into `secrets_encrypted`
    and never returned — mirrors `ConnectorSecret`."""

    __tablename__ = "notifier"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    type: NotifierType
    target: str = Field(default="")
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    secrets_encrypted: str | None = Field(default=None)
    enabled: bool = Field(default=True)


class NotificationRule(TimestampMixin, table=True):
    """An org-scoped rule binding an event to a set of notifiers. `event` is a
    trigger key (e.g. `alert.critical`, `sla.breach_imminent`, `case.assigned`);
    `notifier_ids` lists which channels fire when it matches."""

    __tablename__ = "notification_rule"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    name: str
    description: str = Field(default="")
    event: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    notifier_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))


# --- API I/O ---


class NotifierCreate(SQLModel):
    type: NotifierType
    target: str = ""
    config: dict = {}
    secrets: dict = {}  # write-only; encrypted at rest, never returned
    enabled: bool = True


class NotifierUpdate(SQLModel):
    target: str | None = None
    config: dict | None = None
    secrets: dict | None = None
    enabled: bool | None = None


class NotifierPublic(SQLModel):
    id: uuid.UUID
    type: NotifierType
    target: str
    config: dict
    enabled: bool
    has_secrets: bool = False
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None


class NotificationRuleCreate(SQLModel):
    name: str
    description: str = ""
    event: str | None = None
    enabled: bool = True
    notifier_ids: list[str] = []


class NotificationRuleUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    event: str | None = None
    enabled: bool | None = None
    notifier_ids: list[str] | None = None


class NotificationRulePublic(SQLModel):
    id: uuid.UUID
    name: str
    description: str
    event: str | None
    enabled: bool
    notifier_ids: list[str]
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None


# --- User Notification Feed (A2) ---


class UserNotification(SQLModel, table=True):
    """A row in the per-user notification feed, created by the outbox consumer
    when a matching event fires. `user_id` is null for org-wide notifications."""

    __tablename__ = "user_notification"
    __table_args__ = (
        Index(
            "uq_user_notification_outbox_orgwide",
            "outbox_id",
            unique=True,
            postgresql_where=text("user_id IS NULL AND outbox_id IS NOT NULL"),
        ),
        Index(
            "uq_user_notification_outbox_user",
            "outbox_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND outbox_id IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    user_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", index=True, ondelete="CASCADE"
    )
    #: The audit_outbox row this notification was fanned out from. Part of the
    #: idempotency key so drain retries can't duplicate rows; None for rows
    #: created outside the outbox pipeline.
    outbox_id: int | None = Field(
        default=None, foreign_key="audit_outbox.id", index=True, ondelete="SET NULL"
    )
    event_type: str = Field(index=True)
    title: str
    body: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserNotificationRead(SQLModel, table=True):
    """Per-user read receipt: a row exists iff `user_id` has read
    `notification_id`. Gives org-wide (user_id=None) notifications per-user
    read state instead of one shared read_at."""

    __tablename__ = "user_notification_read"
    __table_args__ = (
        UniqueConstraint(
            "notification_id", "user_id", name="uq_user_notification_read"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    notification_id: uuid.UUID = Field(
        foreign_key="user_notification.id", ondelete="CASCADE"
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", index=True, ondelete="CASCADE"
    )
    read_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserNotificationPublic(SQLModel):
    id: uuid.UUID
    event_type: str
    title: str
    body: str
    payload: dict
    read_at: datetime | None
    created_at: datetime


class UserNotificationUpdate(SQLModel):
    read_at: datetime | None = None  # set to now() to mark read, None for unread


# --- User Notification Preferences (A2) ---


class UserNotificationPreference(TimestampMixin, table=True):
    """Per-user, per-org in-app notification mute setting. A row exists only for
    event types the user has explicitly configured; absence means enabled."""

    __tablename__ = "user_notification_preference"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "organisation_id", "event_type", name="uq_user_notif_pref"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        foreign_key="user.id", index=True, ondelete="CASCADE"
    )
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    event_type: str = Field(index=True)
    enabled: bool = Field(default=True)


class NotificationPreferenceItem(SQLModel):
    event_type: str
    label: str
    category: str
    enabled: bool


class NotificationPreferencesPublic(SQLModel):
    items: list[NotificationPreferenceItem]


class NotificationPreferencesUpdate(SQLModel):
    preferences: dict[str, bool]  # event_type -> enabled


# --- Notifier Delivery (A3) ---


class NotifierDelivery(SQLModel, table=True):
    """One row per (outbox, notifier) pair — the delivery audit trail.
    Unique constraint prevents duplicate sends when the same outbox row
    matches the same notifier on retry."""

    __tablename__ = "notifier_delivery"
    __table_args__ = (
        UniqueConstraint(
            "outbox_id", "notifier_id", name="uq_notifier_delivery_outbox_notifier"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    #: Nullable + SET NULL: pruning the source outbox row nulls this link but
    #: keeps the delivery-ledger row (see migration c4e8f2a6b0d9).
    outbox_id: int | None = Field(
        default=None, foreign_key="audit_outbox.id", index=True, ondelete="SET NULL"
    )
    notifier_id: uuid.UUID = Field(
        foreign_key="notifier.id", index=True, ondelete="CASCADE"
    )
    status: str = Field(default="queued", index=True)  # queued | sent | failed
    attempts: int = Field(default=0)
    last_error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
