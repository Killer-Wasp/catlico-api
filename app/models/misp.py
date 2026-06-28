"""F4: MISP server configuration and import/export models."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class MispServer(TimestampMixin, table=True):
    """An org-scoped MISP server configuration. Secrets are encrypted at rest."""

    __tablename__ = "misp_server"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    name: str
    url: str = Field(default="")
    auth_key_encrypted: str | None = Field(default=None)
    enabled: bool = Field(default=True)
    verify_ssl: bool = Field(default=True)
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))


# --- API I/O ---


class MispServerCreate(SQLModel):
    name: str
    url: str = ""
    auth_key: str = ""  # write-only; encrypted at rest
    enabled: bool = True
    verify_ssl: bool = True
    config: dict = {}


class MispServerUpdate(SQLModel):
    name: str | None = None
    url: str | None = None
    auth_key: str | None = None
    enabled: bool | None = None
    verify_ssl: bool | None = None
    config: dict | None = None


class MispServerPublic(SQLModel):
    id: uuid.UUID
    name: str
    url: str
    enabled: bool
    verify_ssl: bool
    has_auth_key: bool = False
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None


class MispImportRequest(SQLModel):
    """Request to import events from a MISP server."""
    server_id: uuid.UUID
    event_id: str | None = None  # specific event, or None for latest


class MispExportRequest(SQLModel):
    """Request to export case observables to a MISP server."""
    server_id: uuid.UUID
    ioc_only: bool = True  # only export IOC-flagged observables by default
