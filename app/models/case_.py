import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlmodel import Field, SQLModel

from app.models.common import MARKDOWN_NOTE, SoftDeleteMixin, TimestampMixin
from app.models.task import TaskStatus


class CaseStatus(str, Enum):
    open = "Open"
    resolved = "Resolved"
    duplicated = "Duplicated"


class CaseResolutionStatus(str, Enum):
    indeterminate = "Indeterminate"
    false_positive = "FalsePositive"
    true_positive = "TruePositive"
    other = "Other"
    duplicated = "Duplicated"


class CaseImpactStatus(str, Enum):
    no_impact = "NoImpact"
    with_impact = "WithImpact"
    not_applicable = "NotApplicable"


class Case(TimestampMixin, SoftDeleteMixin, table=True):
    __tablename__ = "case_"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    description: str = Field(default="")
    severity: int = Field(default=2)
    tlp: int = Field(default=2)
    pap: int = Field(default=2)
    status: CaseStatus = Field(default=CaseStatus.open)
    assignee_id: uuid.UUID | None = Field(
        default=None, foreign_key="user.id", ondelete="SET NULL"
    )
    start_date: datetime | None = Field(default=None)
    end_date: datetime | None = Field(default=None)
    summary: str | None = Field(default=None)
    resolution_status: CaseResolutionStatus | None = Field(default=None)
    impact_status: CaseImpactStatus | None = Field(default=None)
    duplicate_of_case_id: int | None = Field(
        default=None, foreign_key="case_.id", ondelete="SET NULL"
    )
    # Per-case monotonic counters for the composite-keyed children. Each child
    # insert locks the case row and post-increments the relevant counter, so the
    # allocated ids are gap-free per case and never reused (see app.crud._seq).
    next_task_seq: int = Field(default=1)
    next_attachment_seq: int = Field(default=1)


class CaseCreate(SQLModel):
    title: str
    description: str = Field(default="", description=MARKDOWN_NOTE)
    severity: int = 2
    tlp: int = 2
    pap: int = 2
    assignee_id: uuid.UUID | None = None
    start_date: datetime | None = None
    summary: str | None = Field(default=None, description=MARKDOWN_NOTE)
    # Optional: scaffold the case from a template (scalar defaults + tasks + tags).
    case_template_id: int | None = None


class CaseMergeRequest(SQLModel):
    #: The cases to merge (2+ distinct, all owned by the acting org).
    source_ids: list[int]
    #: Explicit scalar fields for the new (survivor) case. The frontend pre-fills
    #: suggestions (max severity, concatenated title); the human confirms.
    case: CaseCreate


class CaseTaskSummary(SQLModel):
    """Slim task projection embedded in a case for list views: enough for a
    client to compute progress (done/total) without fetching full tasks."""

    id: int
    public_id: str
    title: str
    status: TaskStatus


class CasePublic(SQLModel):
    id: int
    title: str
    description: str = Field(description=MARKDOWN_NOTE)
    severity: int
    tlp: int
    pap: int
    status: CaseStatus
    flagged: bool = False
    assignee_id: uuid.UUID | None
    #: Assignee's email, resolved from assignee_id by a batched lookup at read
    #: time (None when unassigned). Lets list clients show a name without an
    #: extra round-trip per row.
    assignee_email: str | None = None
    #: Tag strings, populated by a batched lookup at read time.
    tags: list[str] = []
    #: Task statuses, populated by a batched lookup at read time. The client
    #: derives done/total from these.
    tasks: list[CaseTaskSummary] = []
    start_date: datetime | None
    end_date: datetime | None
    summary: str | None
    resolution_status: CaseResolutionStatus | None
    impact_status: CaseImpactStatus | None
    duplicate_of_case_id: int | None
    # Merge lineage (from case_merge), populated by a batched lookup at read time.
    merged_into: int | None = None
    merged_from: list[int] = []
    custom_fields: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime | None
    #: Resolve-SLA target time (created_at + the severity policy's resolve_seconds),
    #: or None when the org has no enabled policy for this severity. Populated at
    #: read time.
    sla_due_at: datetime | None = None
    #: SLA state for an OPEN case: "ok" | "at-risk" | "breached". None when the case
    #: is not open or no policy applies. Populated at read time.
    sla_state: str | None = None


class SimilarCasePublic(SQLModel):
    """A case that shares one or more observables with an alert — the alert
    drawer's "Similar cases" list. `shared_observables` is the overlap count."""

    id: int
    title: str
    severity: int
    status: CaseStatus
    shared_observables: int


class LinkedCasePublic(SQLModel):
    """A case the alert is directly linked to (via `Alert.case_id`) — the alert
    drawer's "Linked case" list, mirroring a case's "Linked alerts"."""

    id: int
    title: str
    severity: int
    status: CaseStatus


class CaseCounts(SQLModel):
    """Per-section counts for a single case, powering the detail page's tab
    badges without materialising each section's full list on load."""

    tasks: int = 0
    custom_fields: int = 0
    comments: int = 0
    attachments: int = 0
    observables: int = 0


class CaseListFacets(SQLModel):
    """Filterable values present across an org's case list, for the list view's
    filter dropdowns."""

    #: Distinct assignee emails on the org's cases.
    assignees: list[str] = []
    #: Whether any case is unassigned (offers the "Unassigned" filter option).
    unassigned: bool = False
    #: Filterable tag keys → their distinct values (value-aware grouping; free
    #: tags excluded). E.g. {"tlp": ["amber", "red"], "kill-chain:phase": [...]}.
    tag_keys: dict[str, list[str]] = {}


class CaseUpdate(SQLModel):
    title: str | None = None
    description: str | None = Field(default=None, description=MARKDOWN_NOTE)
    severity: int | None = None
    tlp: int | None = None
    pap: int | None = None
    assignee_id: uuid.UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    summary: str | None = None
    status: CaseStatus | None = None
    resolution_status: CaseResolutionStatus | None = None
    impact_status: CaseImpactStatus | None = None
    duplicate_of_case_id: int | None = None
