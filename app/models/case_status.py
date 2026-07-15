from datetime import datetime
from enum import Enum

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class CaseStage(str, Enum):
    """The four semantic buckets every case status maps onto. Consumers (SLA,
    overview, merge, the merged-away read-only guard) branch on the *stage*, never
    the org-custom label, so an org can rename/add statuses without touching logic.

    - ``open`` / ``in_progress``: the case is live (both count as "open" for SLA).
    - ``closed``: terminal, resolved.
    - ``duplicated``: terminal tombstone left behind by a merge (read-only)."""

    open = "open"
    in_progress = "in_progress"
    closed = "closed"
    duplicated = "duplicated"


class CaseStatus(TimestampMixin, table=True):
    """An org-scoped, admin-defined case status. Replaces the old native PG
    ``casestatus`` enum. Identity is (organisation_id, label). Built-in statuses
    (Open / In progress / Resolved / Duplicated) are seeded per org and protected
    from edit/delete (``is_builtin``); custom statuses can be added, hidden, and
    reordered. A status in use by any case cannot be deleted (FK RESTRICT) — hide
    it instead."""

    __tablename__ = "case_status"
    __table_args__ = (
        Index("uq_case_status_org_label", "organisation_id", "label", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    label: str
    stage: CaseStage
    #: Badge colour (hex, e.g. "#3b82f6"). Rendered by the client; the lookup is
    #: the single source of truth (the old static StatusBadge map is gone).
    color: str = Field(default="#6b7280")
    is_builtin: bool = Field(default=False)
    hidden: bool = Field(default=False)
    position: int = Field(default=0)


class CaseStatusRef(SQLModel):
    """Slim status projection embedded in case read models — enough for a client
    to render the status badge (label + colour) and branch on stage."""

    id: int
    label: str
    stage: CaseStage
    color: str


class CaseStatusCreate(SQLModel):
    label: str
    stage: CaseStage
    color: str = "#6b7280"
    position: int | None = None


class CaseStatusUpdate(SQLModel):
    label: str | None = None
    stage: CaseStage | None = None
    color: str | None = None
    hidden: bool | None = None
    position: int | None = None


class CaseStatusPublic(SQLModel):
    id: int
    organisation_id: str
    label: str
    stage: CaseStage
    color: str
    is_builtin: bool
    hidden: bool
    position: int
    created_at: datetime
    updated_at: datetime | None
