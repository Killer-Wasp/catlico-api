"""B4: Artifact provenance — tracks which enrichment job + connector produced
each imported artifact observable."""

import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ObservableProvenance(SQLModel, table=True):
    __tablename__ = "observable_provenance"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    observable_id: uuid.UUID = Field(
        foreign_key="observable.id", index=True, ondelete="CASCADE"
    )
    source_job_id: uuid.UUID | None = Field(
        default=None, foreign_key="enrichment_job.id", index=True, ondelete="SET NULL"
    )
    connector_name: str = Field(index=True)
    message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
