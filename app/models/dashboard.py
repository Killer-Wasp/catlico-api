"""G3: Dashboard storage CRUD model."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class Dashboard(TimestampMixin, table=True):
    __tablename__ = "dashboard"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    name: str
    description: str = Field(default="")
    layout: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_public: bool = Field(default=False)


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
    created_at: datetime
    updated_at: datetime | None
