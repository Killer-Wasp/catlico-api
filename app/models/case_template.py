import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


class CaseTemplate(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "case_template"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    display_name: str = Field(default="")
    title_prefix: str = Field(default="")
    description: str = Field(default="")
    severity: int | None = Field(default=None)
    tlp: int | None = Field(default=None)
    pap: int | None = Field(default=None)
    summary: str | None = Field(default=None)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )


class CaseTemplateTask(SQLModel, table=True):
    __tablename__ = "case_template_task"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: int = Field(
        foreign_key="case_template.id", index=True, ondelete="CASCADE"
    )
    title: str
    group: str = Field(default="")
    description: str = Field(default="")
    order: int = Field(default=0)


class CaseTemplateTaskIn(SQLModel):
    title: str
    group: str = ""
    description: str = ""
    order: int = 0


class CaseTemplateTaskPublic(SQLModel):
    id: uuid.UUID
    title: str
    group: str
    description: str
    order: int


class CaseTemplateCreate(SQLModel):
    name: str
    display_name: str = ""
    title_prefix: str = ""
    description: str = Field(default="", description=MARKDOWN_NOTE)
    severity: int | None = None
    tlp: int | None = None
    pap: int | None = None
    summary: str | None = Field(default=None, description=MARKDOWN_NOTE)
    tasks: list[CaseTemplateTaskIn] = []


class CaseTemplatePublic(SQLModel):
    id: int
    name: str
    display_name: str
    title_prefix: str
    description: str = Field(description=MARKDOWN_NOTE)
    severity: int | None
    tlp: int | None
    pap: int | None
    summary: str | None
    organisation_id: str
    tasks: list[CaseTemplateTaskPublic] = []
    tags: list[str] = []
    created_at: datetime
    updated_at: datetime | None


class CaseTemplateUpdate(SQLModel):
    display_name: str | None = None
    title_prefix: str | None = None
    description: str | None = Field(default=None, description=MARKDOWN_NOTE)
    severity: int | None = None
    tlp: int | None = None
    pap: int | None = None
    summary: str | None = None
    tasks: list[CaseTemplateTaskIn] | None = None


class CaseTemplateExport(SQLModel):
    """Portable, instance-agnostic representation for import/export — no ids, org or
    timestamps, with a kind/version envelope for forward-compatibility."""

    kind: str = "catlico.caseTemplate"
    version: int = 1
    name: str
    display_name: str = ""
    title_prefix: str = ""
    description: str = Field(default="", description=MARKDOWN_NOTE)
    severity: int | None = None
    tlp: int | None = None
    pap: int | None = None
    summary: str | None = Field(default=None, description=MARKDOWN_NOTE)
    tasks: list[CaseTemplateTaskIn] = []
    tags: list[str] = []
