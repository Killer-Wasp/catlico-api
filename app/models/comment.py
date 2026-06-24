import uuid
from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin


class CommentEntityType(str, Enum):
    case = "case"
    # alert comments are a trivial later add — model is polymorphic-ready.


class Comment(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "comment"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    entity_type: CommentEntityType = Field(index=True)
    entity_id: str = Field(index=True)
    message: str
    # Author org — comments ride the parent's visibility; this records who said it.
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )


class CommentCreate(SQLModel):
    message: str = Field(description=MARKDOWN_NOTE)


def _display_name_from_email(email: str) -> str:
    """Derive a display name from the email's local part (matches frontend logic)."""
    local = email.split("@")[0]
    parts = local.replace(".", " ").replace("-", " ").replace("_", " ").split()
    if not parts:
        return email
    return " ".join(
        p.upper() + "." if len(p) == 1 else p[0].upper() + p[1:] for p in parts
    )


class CommentPublic(SQLModel):
    id: uuid.UUID
    entity_type: CommentEntityType
    entity_id: str
    message: str = Field(description=MARKDOWN_NOTE)
    organisation_id: str
    created_at: datetime
    created_by: str
    updated_at: datetime | None
    author_name: str = ""


class CommentUpdate(SQLModel):
    message: str = Field(description=MARKDOWN_NOTE)
