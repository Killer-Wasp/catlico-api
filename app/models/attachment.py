import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, ForeignKeyConstraint
from sqlmodel import Field, SQLModel

from app.models.common import CreatedMixin, SoftDeleteMixin
from app.util.ids import format_attachment_id


class AttachmentOwnerType(str, Enum):
    """What a case-bound attachment link hangs off. Observable attachments are a
    separate table (ObservableAttachmentLink) — observables span cases, so they
    can't share the per-case counter."""

    case = "case"
    task = "task"
    log = "log"


class Attachment(CreatedMixin, table=True):
    """A content-addressed blob. Deduped by SHA-256 — identical bytes are stored once
    and may be referenced by many links (across owners and cases). The display name
    lives on the link, not here, because the same bytes can be uploaded under
    different names."""

    __tablename__ = "attachment"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    sha256: str = Field(unique=True, index=True)
    size: int
    content_type: str = Field(default="application/octet-stream")


class AttachmentLink(CreatedMixin, SoftDeleteMixin, table=True):
    """A case-bound reference from an owner (the case itself, a task, or a log) to a
    blob, carrying the per-reference display name. Identity is (case_id, id) where
    `id` is a per-case integer from Case.next_attachment_seq — a single counter
    across every owner kind in the case. Display form: A-{case_id}-{id}.

    The owner is expressed with concrete nullable columns (real composite FKs), not a
    polymorphic string, because a composite FK must target one concrete table:
      - case-owned:  owner_task_id IS NULL  AND owner_log_id IS NULL
      - task-owned:  owner_task_id NOT NULL AND owner_log_id IS NULL
      - log-owned:   owner_task_id NOT NULL AND owner_log_id NOT NULL
    """

    __tablename__ = "attachment_link"
    # Deferrable composite FKs so a case merge can re-key these alongside their
    # owners within one transaction (see app.crud.case_.merge_cases).
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id"],
            ["case_.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["case_id", "owner_task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["case_id", "owner_task_id", "owner_log_id"],
            ["log.case_id", "log.task_id", "log.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        # A log owner implies a task owner (TL-…-x-y has both); a case owner has
        # neither. Forbids the only illegal combination: log set without task.
        CheckConstraint(
            "owner_task_id IS NOT NULL OR owner_log_id IS NULL",
            name="ck_attachment_link_owner",
        ),
    )

    case_id: int = Field(primary_key=True, index=True)
    id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    attachment_id: uuid.UUID = Field(foreign_key="attachment.id", ondelete="CASCADE")
    owner_task_id: int | None = Field(default=None)
    owner_log_id: int | None = Field(default=None)
    name: str
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )

    @property
    def owner_type(self) -> AttachmentOwnerType:
        if self.owner_log_id is not None:
            return AttachmentOwnerType.log
        if self.owner_task_id is not None:
            return AttachmentOwnerType.task
        return AttachmentOwnerType.case

    @property
    def public_id(self) -> str:
        return format_attachment_id(self.case_id, self.id)


class ObservableAttachmentLink(CreatedMixin, SoftDeleteMixin, table=True):
    """An observable's file attachment. Separate from AttachmentLink because
    observables span cases (and may belong to an alert, not a case), so they can't
    draw on a per-case counter. Keeps the opaque UUID identity."""

    __tablename__ = "observable_attachment_link"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    attachment_id: uuid.UUID = Field(foreign_key="attachment.id", ondelete="CASCADE")
    observable_id: uuid.UUID = Field(
        foreign_key="observable.id", index=True, ondelete="CASCADE"
    )
    name: str
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )


class AttachmentPublic(SQLModel):
    id: int  # the per-case link id (what callers reference for download/delete)
    public_id: str  # A-{case_id}-{id}
    case_id: int
    attachment_id: uuid.UUID
    owner_type: AttachmentOwnerType
    owner_task_id: int | None = None
    owner_log_id: int | None = None
    name: str
    size: int
    content_type: str
    sha256: str
    organisation_id: str
    created_at: datetime
    created_by: str


class ObservableAttachmentPublic(SQLModel):
    """Observable file attachment projection (kept UUID-identified)."""

    id: uuid.UUID
    attachment_id: uuid.UUID
    observable_id: uuid.UUID
    name: str
    size: int
    content_type: str
    sha256: str
    organisation_id: str
    created_at: datetime
    created_by: str
