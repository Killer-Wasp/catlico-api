from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


# --- Block content (discriminated union, mirrors the web KnowledgeBaseBlock type) ---


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"]
    text: str = PydanticField(description=MARKDOWN_NOTE)
    code: str | None = None


class SectionBlock(BaseModel):
    type: Literal["section"]
    title: str
    items: list[str]


class ListBlock(BaseModel):
    type: Literal["list"]
    items: list[str]


KnowledgeBaseBlock = Annotated[
    ParagraphBlock | SectionBlock | ListBlock,
    PydanticField(discriminator="type"),
]


class KnowledgeBasePage(TimestampMixin, SoftDeleteMixin, table=True):
    """An org-scoped runbook/reference page. Content is an ordered list of typed
    blocks (paragraph/section/list) stored as JSON. `author`/`updated` in the UI
    map to `created_by`/`updated_at` — no separate columns."""

    __tablename__ = "knowledge_base_page"

    id: int | None = Field(default=None, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    title: str = Field(index=True)
    summary: str = Field(default="")
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    blocks: list[dict] = Field(default_factory=list, sa_column=Column(JSON))


class KnowledgeBasePageCreate(SQLModel):
    title: str
    summary: str = ""
    tags: list[str] = []
    blocks: list[KnowledgeBaseBlock] = []


class KnowledgeBasePageUpdate(SQLModel):
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    blocks: list[KnowledgeBaseBlock] | None = None


class KnowledgeBasePagePublic(SQLModel):
    id: int
    title: str
    summary: str
    tags: list[str]
    blocks: list[KnowledgeBaseBlock]
    organisation_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime | None
