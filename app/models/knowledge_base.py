from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import JSON, CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


class KnowledgeBasePage(TimestampMixin, SoftDeleteMixin, table=True):
    """An org-scoped runbook/reference page."""

    __tablename__ = "knowledge_base_page"

    id: int | None = Field(default=None, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    title: str = Field(index=True)
    summary: str = Field(default="")
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    content: str = Field(default="", description=MARKDOWN_NOTE)


KnowledgeBaseVersionAction = Literal["create", "update", "revert", "import"]


class KnowledgeBaseContributor(SQLModel):
    id: str
    email: str
    last_edited_at: datetime


class KnowledgeBasePageVersion(TimestampMixin, table=True):
    __tablename__ = "knowledge_base_page_version"
    __table_args__ = (
        UniqueConstraint(
            "page_id", "version_number", name="uq_kb_page_version_page_number"
        ),
        CheckConstraint(
            "action IN ('create', 'update', 'revert', 'import')",
            name="ck_kb_page_version_action",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    page_id: int = Field(
        foreign_key="knowledge_base_page.id", index=True, ondelete="CASCADE"
    )
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    version_number: int = Field(index=True)
    action: KnowledgeBaseVersionAction = Field(
        sa_column=Column(String, nullable=False, index=True)
    )
    snapshot: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
    )
    changed_fields: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default="[]"),
    )
    edited_by: str
    edited_by_email: str
    edited_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    reverted_from_version_id: int | None = Field(
        default=None, foreign_key="knowledge_base_page_version.id"
    )
    created_by: str = Field(default="system")


class KnowledgeBasePageCreate(SQLModel):
    title: str
    summary: str = ""
    tags: list[str] = []
    content: str = Field(default="", description=MARKDOWN_NOTE)


class KnowledgeBasePageUpdate(SQLModel):
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    content: str | None = Field(default=None, description=MARKDOWN_NOTE)


class KnowledgeBasePagePublic(SQLModel):
    id: int
    title: str
    summary: str
    tags: list[str]
    content: str
    organisation_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime | None
    contributors: list[KnowledgeBaseContributor] = []
    last_edited_by: KnowledgeBaseContributor | None = None


class KnowledgeBasePageVersionPublic(SQLModel):
    id: int
    page_id: int
    version_number: int
    action: KnowledgeBaseVersionAction
    snapshot: dict
    changed_fields: list[str]
    edited_by: str
    edited_by_email: str
    edited_at: datetime
    reverted_from_version_id: int | None = None


class KnowledgeBasePageExport(SQLModel):
    page: KnowledgeBasePagePublic
    versions: list[KnowledgeBasePageVersionPublic]


class KnowledgeBasePageImport(SQLModel):
    page: KnowledgeBasePageCreate
    versions: list[KnowledgeBasePageVersionPublic] = []
