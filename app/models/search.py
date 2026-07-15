import uuid
from datetime import datetime

from sqlmodel import SQLModel

from app.models.alert import AlertStatus
from app.models.case_status import CaseStatusRef
from app.models.comment import CommentEntityType
from app.models.task import TaskStatus

#: Wire names for searchable entity types (the `types` query param and the
#: keys of counts/results).
SEARCH_TYPES = (
    "case",
    "alert",
    "observable",
    "task",
    "comment",
    "knowledge_base",
    "attachment",
)


class CaseHit(SQLModel):
    id: int
    title: str
    snippet: str
    status: CaseStatusRef | None = None
    severity: int
    updated_at: datetime | None
    created_at: datetime


class AlertHit(SQLModel):
    id: int
    title: str
    snippet: str
    status: AlertStatus
    severity: int


class ObservableHit(SQLModel):
    id: uuid.UUID
    observable_type: str
    data: str
    case_id: int | None
    alert_id: int | None
    ioc: bool
    message: str
    verdict: str | None


class ObservableGroupHit(SQLModel):
    """Palette-mode observable row: one per distinct value, with its
    occurrence count across the org's visible observables."""

    observable_type: str
    data: str
    occurrences: int


class TaskHit(SQLModel):
    case_id: int
    id: int
    public_id: str
    title: str
    status: TaskStatus


class CommentHit(SQLModel):
    id: uuid.UUID
    entity_type: CommentEntityType
    entity_id: str
    snippet: str
    author_name: str
    created_at: datetime


class KnowledgeBaseHit(SQLModel):
    id: int
    title: str
    snippet: str


class AttachmentHit(SQLModel):
    #: The per-case link id (what callers reference for download/delete).
    id: int
    #: A-{case_id}-{id}
    public_id: str
    case_id: int
    attachment_id: uuid.UUID
    name: str


class SearchCounts(SQLModel):
    case: int = 0
    alert: int = 0
    observable: int = 0
    task: int = 0
    comment: int = 0
    knowledge_base: int = 0
    attachment: int = 0


class SearchResults(SQLModel):
    case: list[CaseHit] = []
    alert: list[AlertHit] = []
    #: Per-occurrence rows (results page) — mutually exclusive with
    #: observable_groups (palette mode).
    observable: list[ObservableHit] = []
    observable_groups: list[ObservableGroupHit] = []
    task: list[TaskHit] = []
    comment: list[CommentHit] = []
    knowledge_base: list[KnowledgeBaseHit] = []
    attachment: list[AttachmentHit] = []


class SearchResponse(SQLModel):
    counts: SearchCounts
    results: SearchResults
