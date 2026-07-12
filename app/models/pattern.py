"""F1: MITRE ATT&CK pattern and procedure models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class Pattern(TimestampMixin, table=True):
    """A MITRE ATT&CK technique/tactic record. `external_id` is the canonical
    ID (e.g. T1059 for Command and Scripting Interpreter)."""

    __tablename__ = "pattern"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    external_id: str = Field(index=True, unique=True)
    name: str = Field(index=True)
    description: str = Field(default="")
    tactics: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    url: str = Field(default="")
    parent_external_id: str | None = Field(default=None)


class Procedure(TimestampMixin, table=True):
    """Links a case to a MITRE pattern — "we observed T1059 in this case"."""

    __tablename__ = "procedure"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    case_id: int = Field(foreign_key="case_.id", index=True, ondelete="CASCADE")
    pattern_id: uuid.UUID = Field(foreign_key="pattern.id", ondelete="CASCADE")
    description: str = Field(default="")


# --- API I/O ---


class PatternCreate(SQLModel):
    external_id: str
    name: str
    description: str = ""
    tactics: list[str] = []
    url: str = ""
    parent_external_id: str | None = None


class PatternImportItem(PatternCreate):
    """One record in a bulk import. Upserted by external_id."""


class AttackImportResult(SQLModel):
    """Outcome of a server-side MITRE catalog import."""

    created: int
    updated: int
    total: int


class PatternPublic(SQLModel):
    id: uuid.UUID
    external_id: str
    name: str
    description: str
    tactics: list[str]
    url: str
    parent_external_id: str | None
    created_at: datetime


class ProcedurePublic(SQLModel):
    id: uuid.UUID
    case_id: int
    pattern_id: uuid.UUID
    pattern: PatternPublic | None = None
    description: str
    created_at: datetime


class PatternCaseSummary(SQLModel):
    """Slim case row for matrix click-through — deliberately not CasePublic."""

    id: int
    title: str
    severity: int
    status: str
    created_at: datetime


class ProcedureReplace(SQLModel):
    """Replace all procedures for a case — the request body is a list of patterns
    to link (by external_id) with optional descriptions."""
    procedures: list[PatternImportItem] = []
