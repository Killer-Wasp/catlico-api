import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column
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
