from sqlalchemy import ForeignKeyConstraint
from sqlmodel import Field

from app.models.common import CreatedMixin


class TaskShare(CreatedMixin, table=True):
    __tablename__ = "task_share"
    # Composite FK to the task's composite key.
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "task_id"],
            ["task.case_id", "task.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    case_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    task_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
