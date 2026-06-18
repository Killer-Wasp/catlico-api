import uuid
from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import SoftDeleteMixin, TimestampMixin


class TaskStatus(str, Enum):
    waiting = "Waiting"
    in_progress = "InProgress"
    completed = "Completed"
    cancelled = "Cancelled"


#: Allowed status transitions (improvement over TheHive4's any->any).
#: Waiting -> InProgress; InProgress -> {Waiting, Completed, Cancelled};
#: terminal states (Completed/Cancelled) are re-openable back to Waiting.
TASK_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.waiting: {TaskStatus.in_progress},
    TaskStatus.in_progress: {
        TaskStatus.waiting,
        TaskStatus.completed,
        TaskStatus.cancelled,
    },
    TaskStatus.completed: {TaskStatus.waiting},
    TaskStatus.cancelled: {TaskStatus.waiting},
}

TASK_TERMINAL_STATUSES = {TaskStatus.completed, TaskStatus.cancelled}


class Task(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "task"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    public_id: str = Field(index=True, unique=True)
    case_id: int = Field(foreign_key="case_.id", index=True, ondelete="CASCADE")
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="RESTRICT"
    )
    title: str
    group: str = Field(default="")
    description: str = Field(default="")
    status: TaskStatus = Field(default=TaskStatus.waiting)
    assignee_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    order: int = Field(default=0)
    start_date: datetime | None = Field(default=None)
    due_date: datetime | None = Field(default=None)
    # Auto-managed: set when status enters a terminal state, cleared on re-open.
    end_date: datetime | None = Field(default=None)


class TaskCreate(SQLModel):
    title: str
    group: str = ""
    description: str = ""
    assignee_id: uuid.UUID | None = None
    order: int = 0
    start_date: datetime | None = None
    due_date: datetime | None = None


class TaskPublic(SQLModel):
    id: uuid.UUID
    public_id: str
    case_id: int
    organisation_id: str
    title: str
    group: str
    description: str
    status: TaskStatus
    assignee_id: uuid.UUID | None
    order: int
    flagged: bool = False
    start_date: datetime | None
    due_date: datetime | None
    end_date: datetime | None
    created_at: datetime
    updated_at: datetime | None


class TaskUpdate(SQLModel):
    title: str | None = None
    group: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None
    order: int | None = None
    start_date: datetime | None = None
    due_date: datetime | None = None
