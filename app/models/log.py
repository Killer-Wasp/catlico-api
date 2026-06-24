from datetime import datetime

from sqlalchemy import ForeignKeyConstraint
from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin
from app.util.ids import format_log_id


class Log(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "log"
    # Composite FK to the parent task's composite key.
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            # Deferrable so a case merge can re-key parent and children within one
            # transaction (children updated before the parent settles).
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    # Composite identity: (case_id, task_id, id). `id` is a per-task integer
    # allocated from Task.next_log_seq, so it resets within each task. Display
    # form is derived: TL-{case_id}-{task_id}-{id}.
    case_id: int = Field(primary_key=True, index=True)
    task_id: int = Field(primary_key=True, index=True)
    id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )
    message: str
    # Analyst-set time the event actually happened (may be backdated). Distinct from
    # created_at, which is the immutable time the entry was typed. Defaults to created_at.
    occurred_at: datetime | None = Field(default=None)

    @property
    def public_id(self) -> str:
        return format_log_id(self.case_id, self.task_id, self.id)


class LogCreate(SQLModel):
    message: str = Field(description=MARKDOWN_NOTE)
    occurred_at: datetime | None = None


class LogPublic(SQLModel):
    id: int
    public_id: str
    case_id: int
    task_id: int
    organisation_id: str
    message: str = Field(description=MARKDOWN_NOTE)
    occurred_at: datetime | None
    created_at: datetime
    created_by: str
    updated_at: datetime | None


class LogUpdate(SQLModel):
    message: str | None = Field(default=None, description=MARKDOWN_NOTE)
    occurred_at: datetime | None = None
