import uuid
from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import CreatedMixin, SoftDeleteMixin


class AttachmentOwnerType(str, Enum):
    observable = "observable"
    log = "log"
    case = "case"


class Attachment(CreatedMixin, table=True):
    """A content-addressed blob. Deduped by SHA-256 — identical bytes are stored once
    and may be referenced by many attachment_links. The display name lives on the link,
    not here, because the same bytes can be uploaded under different names."""

    __tablename__ = "attachment"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sha256: str = Field(unique=True, index=True)
    size: int
    content_type: str = Field(default="application/octet-stream")


class AttachmentLink(CreatedMixin, SoftDeleteMixin, table=True):
    """Polymorphic reference from an owner (observable | log) to a blob. Carries the
    per-reference display name. Rides the owner's visibility — no separate ACL."""

    __tablename__ = "attachment_link"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    attachment_id: uuid.UUID = Field(foreign_key="attachment.id", ondelete="CASCADE")
    owner_type: AttachmentOwnerType = Field(index=True)
    owner_id: str = Field(index=True)
    name: str
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )


class AttachmentPublic(SQLModel):
    id: uuid.UUID  # the link id (what callers reference for download/delete)
    attachment_id: uuid.UUID
    owner_type: AttachmentOwnerType
    owner_id: str
    name: str
    size: int
    content_type: str
    sha256: str
    organisation_id: str
    created_at: datetime
    created_by: str
