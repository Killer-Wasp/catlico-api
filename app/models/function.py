import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.common import CreatedMixin, SoftDeleteMixin, TimestampMixin


class FunctionRuntime(str, Enum):
    javascript = "javascript"
    python = "python"


class FunctionTrigger(str, Enum):
    scheduled = "scheduled"
    event = "event"
    manual = "manual"
    api = "api"


class FunctionRunStatus(str, Enum):
    success = "success"
    failure = "failure"


class Function(TimestampMixin, SoftDeleteMixin, table=True):
    """An org-scoped automation: user code run by the engine as a pinned profile,
    fired by a schedule/event/manual action/API call. `trigger_config` holds the
    trigger-specific shape (cron / condition / entities). `secrets` are vault
    reference names (not values). `run_count`/`error_count` are denormalised
    counters maintained when runs are recorded (execution is a later milestone)."""

    __tablename__ = "function"

    id: int | None = Field(default=None, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    name: str = Field(index=True)
    description: str = Field(default="")
    runtime: FunctionRuntime = Field(default=FunctionRuntime.javascript)
    trigger: FunctionTrigger = Field(default=FunctionTrigger.event)
    trigger_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    profile: str = Field(default="analyst")
    enabled: bool = Field(default=False)
    timeout_ms: int = Field(default=15000)
    egress: str = Field(default="")
    approval: bool = Field(default=False)
    code: str = Field(default="")
    secrets: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    run_count: int = Field(default=0)
    error_count: int = Field(default=0)


class FunctionRun(CreatedMixin, table=True):
    """One immutable execution record of a function."""

    __tablename__ = "function_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    function_id: int = Field(
        foreign_key="function.id", index=True, ondelete="CASCADE"
    )
    status: FunctionRunStatus
    trigger: str
    started_at: datetime
    duration_ms: int
    attempts: int = Field(default=1)
    error: str | None = Field(default=None)


# --- API I/O ---


class FunctionCreate(SQLModel):
    name: str
    description: str = ""
    runtime: FunctionRuntime = FunctionRuntime.javascript
    trigger: FunctionTrigger = FunctionTrigger.event
    trigger_config: dict = {}
    profile: str = "analyst"
    enabled: bool = False
    timeout_ms: int = 15000
    egress: str = ""
    approval: bool = False
    code: str = ""
    secrets: list[str] = []


class FunctionUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    runtime: FunctionRuntime | None = None
    trigger: FunctionTrigger | None = None
    trigger_config: dict | None = None
    profile: str | None = None
    enabled: bool | None = None
    timeout_ms: int | None = None
    egress: str | None = None
    approval: bool | None = None
    code: str | None = None
    secrets: list[str] | None = None


class FunctionRunPublic(SQLModel):
    id: uuid.UUID
    status: FunctionRunStatus
    trigger: str
    started_at: datetime
    duration_ms: int
    attempts: int
    error: str | None


class FunctionPublic(SQLModel):
    id: int
    name: str
    description: str
    runtime: FunctionRuntime
    trigger: FunctionTrigger
    trigger_config: dict
    profile: str
    enabled: bool
    timeout_ms: int
    egress: str
    approval: bool
    code: str
    secrets: list[str]
    run_count: int
    error_count: int
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None
    runs: list[FunctionRunPublic] = []
