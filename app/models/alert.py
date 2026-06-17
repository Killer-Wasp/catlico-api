import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel

from app.models.common import SoftDeleteMixin, TimestampMixin


class AlertStatus(str, Enum):
    new = "New"
    in_progress = "InProgress"
    imported = "Imported"
    ignored = "Ignored"


#: Allowed status transitions (improvement over TH4's read/follow booleans).
#: New <-> InProgress; either can go to a terminal state; terminals reopen to New.
#: `Imported` is set automatically by promotion, not via PATCH.
ALERT_STATUS_TRANSITIONS: dict[AlertStatus, set[AlertStatus]] = {
    AlertStatus.new: {AlertStatus.in_progress, AlertStatus.ignored},
    AlertStatus.in_progress: {AlertStatus.new, AlertStatus.ignored},
    AlertStatus.imported: {AlertStatus.new},
    AlertStatus.ignored: {AlertStatus.new, AlertStatus.in_progress},
}


class Alert(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "alert"
    # Per-org dedup key: the same source event ingested into two orgs is two alerts.
    # Partial unique index so a soft-deleted alert doesn't block re-ingesting its key.
    __table_args__ = (
        Index(
            "uq_alert_dedup",
            "type",
            "source",
            "source_ref",
            "organisation_id",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    source: str = Field(index=True)
    source_ref: str
    external_link: str | None = Field(default=None)
    title: str
    description: str = Field(default="")
    severity: int = Field(default=2)
    tlp: int = Field(default=2)
    pap: int = Field(default=2)
    # date = when the event happened (immutable). last_sync_date = last source touch.
    date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_sync_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: AlertStatus = Field(default=AlertStatus.new)
    follow: bool = Field(default=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    # Set when promoted to a case (and status -> Imported).
    case_id: int | None = Field(default=None, foreign_key="case_.id", ondelete="SET NULL")
    assignee_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )


class AlertCreate(SQLModel):
    type: str
    source: str
    source_ref: str
    title: str
    description: str = ""
    severity: int = 2
    tlp: int = 2
    pap: int = 2
    external_link: str | None = None
    date: datetime | None = None
    follow: bool = True


class AlertPublic(SQLModel):
    id: int
    type: str
    source: str
    source_ref: str
    external_link: str | None
    title: str
    description: str
    severity: int
    tlp: int
    pap: int
    date: datetime
    last_sync_date: datetime
    status: AlertStatus
    follow: bool
    flagged: bool = False
    organisation_id: str
    case_id: int | None
    assignee_id: uuid.UUID | None
    custom_fields: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime | None


class AlertUpdate(SQLModel):
    title: str | None = None
    description: str | None = None
    severity: int | None = None
    tlp: int | None = None
    pap: int | None = None
    external_link: str | None = None
    status: AlertStatus | None = None
    follow: bool | None = None
    assignee_id: uuid.UUID | None = None


class AlertPromote(SQLModel):
    """Optional overrides when promoting an alert into a case."""

    title: str | None = None
    assignee_id: uuid.UUID | None = None
    case_template_id: int | None = None


class AlertBulkMerge(SQLModel):
    """Merge 1+ alerts into a single case. With `target_case_id` the alerts are
    imported into that existing case; without it a new case is created from the
    alerts (severity/TLP/PAP = max, description concatenated). The new-case fields
    below are ignored when `target_case_id` is set. See docs/case-merge-design.md."""

    alert_ids: list[int]
    target_case_id: int | None = None
    title: str | None = None
    assignee_id: uuid.UUID | None = None
    case_template_id: int | None = None
