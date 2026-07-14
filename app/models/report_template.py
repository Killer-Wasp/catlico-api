"""G4: Report template model."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class ReportTemplate(TimestampMixin, table=True):
    __tablename__ = "report_template"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", index=True, ondelete="CASCADE")
    name: str
    description: str = Field(default="")
    content_md: str = Field(default="")  # Markdown template with {{ placeholders }}
    config: dict = Field(default_factory=dict, sa_column=Column(JSON))


class ReportTemplateCreate(SQLModel):
    name: str
    description: str = ""
    content_md: str = ""
    config: dict = {}


class ReportTemplateUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    content_md: str | None = None
    config: dict | None = None


class ReportTemplatePublic(SQLModel):
    id: uuid.UUID
    name: str
    description: str
    # Templates aren't secret and this list is admin-adjacent, so the body rides
    # the public shape — a management UI needs it to load a template for editing.
    content_md: str
    config: dict
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None
