import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, SQLModel

from app.models.connector import Verdict
from app.models.observable import ObservableCreate


class JobStatus(str, Enum):
    queued = "queued"
    leased = "leased"
    success = "success"
    failure = "failure"
    cancelled = "cancelled"


class EnrichmentJob(SQLModel, table=True):
    __tablename__ = "enrichment_job"
    __table_args__ = (
        Index("ix_enrichment_job_cache_key", "cache_key"),
        Index("ix_enrichment_job_status", "status"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    observable_id: uuid.UUID = Field(foreign_key="observable.id", ondelete="CASCADE")
    connector_name: str = Field(foreign_key="connector.name")
    connector_version: str = Field(default="")
    # Snapshots so the lease payload is self-contained and the cache survives
    # observable deletion.
    data_type: str
    data: str
    tlp: int = Field(default=2)
    pap: int = Field(default=2)
    status: str = Field(default=JobStatus.queued.value)
    cache_key: str = Field(default="")
    verdict: str | None = Field(default=None)
    report: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    from_cache: bool = Field(default=False)
    attempts: int = Field(default=0)
    lease_token: uuid.UUID | None = Field(default=None)
    lease_expires_at: datetime | None = Field(default=None)
    queued_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    created_by: str = Field(default="")


class ReportTag(SQLModel, table=True):
    """A verdict badge attached to an observable by a connector result."""

    __tablename__ = "report_tag"
    __table_args__ = (Index("ix_report_tag_observable_id", "observable_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    observable_id: uuid.UUID = Field(foreign_key="observable.id", ondelete="CASCADE")
    job_id: uuid.UUID = Field(foreign_key="enrichment_job.id", ondelete="CASCADE")
    connector_name: str = Field(index=True)
    namespace: str = Field(default="")
    predicate: str = Field(default="")
    value: str = Field(default="")
    level: str = Field(default=Verdict.info.value)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# --- API I/O ---


class TaxonomyIn(SQLModel):
    namespace: str = ""
    predicate: str = ""
    value: str = ""
    level: str = "info"


class ResultSubmit(SQLModel):
    lease_token: uuid.UUID
    status: str  # "success" | "failure"
    verdict: str | None = None
    taxonomies: list[TaxonomyIn] = []
    artifacts: list[ObservableCreate] = []
    full: dict = {}
    error: str | None = None


class WorkItem(SQLModel):
    job_id: uuid.UUID
    lease_token: uuid.UUID
    connector_name: str
    connector_version: str
    data_type: str
    data: str
    tlp: int
    pap: int
    config: dict = {}  # decrypted global settings + secrets for the connector


class WorkClaim(SQLModel):
    items: list[WorkItem]


class EnrichmentJobPublic(SQLModel):
    id: uuid.UUID
    observable_id: uuid.UUID
    connector_name: str
    connector_version: str
    status: str
    verdict: str | None
    error: str | None
    from_cache: bool
    queued_at: datetime
    ended_at: datetime | None


class ReportTagPublic(SQLModel):
    connector_name: str
    namespace: str
    predicate: str
    value: str
    level: str


class EnrichRequest(SQLModel):
    connector: str | None = None
    force_refresh: bool = False


class EnrichmentOverview(SQLModel):
    jobs: list[EnrichmentJobPublic]
    tags: list[ReportTagPublic]


class EnrichmentJobRow(SQLModel):
    """One row in the org-wide analyzer-jobs queue. Carries the snapshotted
    observable type/value so the queue renders without joining the (possibly
    deleted) observable, plus the connector's display name for the table."""

    id: uuid.UUID
    observable_id: uuid.UUID
    connector_name: str
    connector_display_name: str
    connector_version: str
    data_type: str
    data: str
    status: str
    verdict: str | None
    error: str | None
    from_cache: bool
    attempts: int
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class EnrichmentJobDetail(EnrichmentJobRow):
    """Full job record for the report drawer: adds the connector report payload,
    the verdict badges it emitted, and the dispatch context."""

    tlp: int
    pap: int
    report: dict | None
    tags: list[ReportTagPublic]
    created_by: str
