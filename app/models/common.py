from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlmodel import Field, SQLModel

T = TypeVar("T")


class CreatedMixin(SQLModel):
    """Creation metadata. Subclasses that need a non-default author (e.g.
    system/analyzer-seeded rows) re-declare just `created_by`."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None))
    created_by: str


class TimestampMixin(CreatedMixin):
    """Full created_/updated_ metadata — the common case for mutable entities."""

    updated_at: datetime | None = Field(default=None)
    updated_by: str | None = Field(default=None)


class SoftDeleteMixin(SQLModel):
    """Soft-delete metadata. Rows are flagged, not removed; every read must filter
    `deleted_at IS NULL`. Mix in alongside a Created/Timestamp mixin."""

    deleted_at: datetime | None = Field(default=None, index=True)
    deleted_by: str | None = Field(default=None)

#: Schema note for rich-text fields. Rendered as markdown by the UI, which MUST
#: sanitise the resulting HTML to prevent stored XSS.
MARKDOWN_NOTE = (
    "Markdown (CommonMark). Rendered as rich text by the UI — the client MUST "
    "sanitise the rendered HTML (e.g. DOMPurify) to prevent stored XSS."
)


class Page(BaseModel, Generic[T]):
    """Offset-paginated list envelope returned by all list endpoints."""

    items: list[T]
    total: int
    skip: int
    limit: int
