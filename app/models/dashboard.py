"""G3: Dashboard storage CRUD model."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin
from app.models.overview import OverviewPublic


class Dashboard(TimestampMixin, table=True):
    __tablename__ = "dashboard"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    name: str
    description: str = Field(default="")
    layout: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_public: bool = Field(default=False)
    #: SHA-256 (hex) of the read-only public share token, or NULL when no share
    #: link is active. The plaintext token is shown once at mint time and never
    #: stored; a lookup hashes the presented token and matches this column.
    share_token_hash: str | None = Field(default=None, index=True)


class DashboardCreate(SQLModel):
    name: str
    description: str = ""
    layout: dict = {}
    is_public: bool = False


class DashboardUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    layout: dict | None = None
    is_public: bool | None = None


class DashboardPublic(SQLModel):
    id: uuid.UUID
    name: str
    description: str
    layout: dict
    #: True when the dashboard is shared with the whole organisation; otherwise
    #: it is private to its owner (`created_by`).
    is_public: bool
    organisation_id: str
    #: Owner user id (the creator). Private dashboards are visible only to them.
    created_by: str
    #: Whether the requesting user owns this dashboard (may edit/share/delete).
    is_owner: bool = False
    #: Owner's display name, for the "shared by" hint on org dashboards.
    owner_name: str | None = None
    #: True when a public read-only share link is active for this dashboard.
    share_enabled: bool = False
    created_at: datetime
    updated_at: datetime | None


class DashboardShareToken(SQLModel):
    """Returned once, at mint time. The plaintext token is never persisted or
    returned again — only its hash is stored."""

    token: str


class PublicDashboardView(SQLModel):
    """The unauthenticated read-only payload served for a valid share token:
    just the board's presentation + the org-scoped overview aggregates. No owner,
    org id, or other tenancy metadata is exposed."""

    name: str
    description: str
    layout: dict
    overview: OverviewPublic
