import uuid
from datetime import datetime

from sqlalchemy import ForeignKeyConstraint
from sqlmodel import Field, SQLModel

from app.models.common import utcnow


class TaskAssignee(SQLModel, table=True):
    """Collaborator (secondary assignee) on a task. The primary owner stays on
    ``Task.assignee_id``; this join table holds the additional collaborators. The
    composite PK (case_id, task_id, user_id) enforces the unique pair. The row is
    deleted when either the task or the user is deleted (CASCADE)."""

    __tablename__ = "task_assignee"
    # Composite FK to the task's composite key (case_id, id).
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
    user_id: uuid.UUID = Field(
        foreign_key="user.id", primary_key=True, ondelete="CASCADE"
    )
    created_at: datetime = Field(default_factory=utcnow)
