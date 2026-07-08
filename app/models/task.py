import uuid
from datetime import datetime
from enum import Enum

from sqlmodel import Field, SQLModel

from app.models.common import SoftDeleteMixin, TimestampMixin
from app.util.ids import format_task_id


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

    # Composite identity: a task is (case_id, id), where `id` is a per-case
    # integer allocated from Case.next_task_seq. No surrogate UUID — case
    # locality is structural. Display form is derived: T-{case_id}-{id}.
    case_id: int = Field(
        foreign_key="case_.id", primary_key=True, index=True, ondelete="CASCADE"
    )
    id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
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
    # Per-task counter for worklogs (Log.id is scoped to its task).
    next_log_seq: int = Field(default=1)

    @property
    def public_id(self) -> str:
        return format_task_id(self.case_id, self.id)


class TaskCreate(SQLModel):
    title: str
    group: str = ""
    description: str = ""
    assignee_id: uuid.UUID | None = None
    order: int = 0
    start_date: datetime | None = None
    due_date: datetime | None = None


class TaskPublic(SQLModel):
    id: int
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
    #: Live work-log count, populated by a batched lookup in the list view so the
    #: client can show an "N logs" hint without loading each task's logs.
    log_count: int = 0
    start_date: datetime | None
    due_date: datetime | None
    end_date: datetime | None
    created_at: datetime
    updated_at: datetime | None


class TaskQueuePublic(TaskPublic):
    """A task plus the case + assignee context the global task queue renders,
    so the cross-case list needs no per-row round-trips."""

    case_title: str
    case_severity: int
    assignee_email: str | None = None


class TaskQueueFacets(SQLModel):
    """Filterable values across the org's task queue, for the list's dropdowns."""

    #: Distinct assignee emails on visible tasks.
    assignees: list[str] = []
    #: Whether any visible task is unassigned.
    unassigned: bool = False
    #: Distinct task kinds (the `group`, with empty shown as "General").
    kinds: list[str] = []


class TaskUpdate(SQLModel):
    title: str | None = None
    group: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None
    order: int | None = None
    start_date: datetime | None = None
    due_date: datetime | None = None
