"""Plugin runner, plugin definition, and plugin run data models.

Phase 1: introduce alongside existing connector models without removing them.
"""
import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class PluginRunnerStatus(str, Enum):
    healthy = "healthy"
    unhealthy = "unhealthy"
    offline = "offline"


class PluginRunner(TimestampMixin, table=True):
    """A registered plugin runner instance (like a Grafana backend plugin host)."""

    __tablename__ = "plugin_runner"

    id: str = Field(primary_key=True)
    name: str = Field(default="")
    base_url: str = Field(default="")
    status: str = Field(default=PluginRunnerStatus.healthy.value)
    version: str = Field(default="")
    capabilities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    isolation_mode: str = Field(default="container")
    last_health_at: datetime | None = Field(default=None)
    last_heartbeat_at: datetime | None = Field(default=None)
    last_error: str | None = Field(default=None)
    created_by: str = Field(default="")


# --- API I/O ---


class PluginRunnerRegister(SQLModel):
    """Body for POST /api/internal/plugin-runner/register."""

    id: str
    name: str = ""
    version: str = ""
    # The URL the API uses to reach this runner for event/install pushes. The
    # runner self-reports it at registration (there is no admin pre-provisioning
    # step to set it any more).
    base_url: str = ""
    capabilities: list[str] = []
    isolation_mode: str = "container"
    plugins: list[dict] = []  # installed plugin manifests


class PluginRunnerHeartbeat(SQLModel):
    """Body for POST /api/internal/plugin-runner/heartbeat."""

    runner_id: str
    capacity: int = 0
    active_run_count: int = 0
    installed_plugin_count: int = 0
    health_summary: str = "ok"


class RunnablePluginPublic(SQLModel):
    """Slim view for GET /plugins/runnable — the plugins an analyst can dispatch
    on demand right now (would not 409 from a manual run), gated on run:enrichment
    rather than read:connector."""

    id: str
    name: str
    description: str = ""
    capabilities: list[str] = []


class PluginVersionRunner(SQLModel):
    """A runner hosting a plugin version, for the Versions tab."""

    id: str
    name: str = ""
    status: str = ""
    install_status: str = ""
    health_status: str | None = None


class PluginVersionInfo(SQLModel):
    """Public view for GET /plugins/{plugin_id}/versions — the installed version
    metadata the web Versions tab renders. ``installed_version`` is null when the
    plugin is in the catalog but not installed on any runner."""

    plugin_id: str
    installed_version: str | None = None
    installed_version_id: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    source_ref: str | None = None
    commit_sha: str | None = None
    status: str | None = None
    installed_at: datetime | None = None
    runners: list[PluginVersionRunner] = []


class PluginLatestCheck(SQLModel):
    """Public view for GET /plugins/{plugin_id}/versions/check-latest.

    Best-effort: ``status`` is ``up_to_date | update_available | unknown``.
    ``latest_version`` is null (and ``reason`` populated) whenever the source
    cannot be consulted — the endpoint never errors on a failed/absent check."""

    plugin_id: str
    installed_version: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    status: str = "unknown"  # up_to_date | update_available | unknown
    reason: str | None = None


# --- Plugin definition & versioning ---


class PluginDefinition(TimestampMixin, table=True):
    """A plugin known to Catlico. Global catalog row; orgs opt in separately."""

    __tablename__ = "plugin_definition"

    id: str = Field(primary_key=True)
    display_name: str = Field(default="")
    description: str = Field(default="")
    active_version_id: str | None = Field(default=None, foreign_key="plugin_version.id")
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON))
    available: bool = Field(default=True)
    created_by: str = Field(default="")


class PluginVersion(TimestampMixin, table=True):
    """One installed immutable plugin version."""

    __tablename__ = "plugin_version"

    id: str = Field(primary_key=True)  # "{plugin_id}@{version}"
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    version: str
    source_type: str = Field(default="github")
    source_url: str = Field(default="")
    source_ref: str = Field(default="")
    commit_sha: str = Field(default="")
    image_digest: str = Field(default="")
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON))
    installed_at: datetime | None = Field(default=None)
    activated_at: datetime | None = Field(default=None)
    status: str = Field(default="installed")  # installed | active | failed
    install_log: str | None = Field(default=None)
    health_status: str | None = Field(default=None)
    created_by: str = Field(default="")


class RunnerPluginInstallation(TimestampMixin, table=True):
    """A runner hosts a specific immutable plugin version."""

    __tablename__ = "runner_plugin_installation"

    runner_id: str = Field(foreign_key="plugin_runner.id", primary_key=True)
    plugin_version_id: str = Field(foreign_key="plugin_version.id", primary_key=True)
    install_status: str = Field(default="installed")
    health_status: str | None = Field(default=None)
    installed_at: datetime | None = Field(default=None)
    last_seen_at: datetime | None = Field(default=None)
    created_by: str = Field(default="")


class OrgPlugin(TimestampMixin, table=True):
    """Per-org enablement and automation settings for a plugin."""

    __tablename__ = "org_plugin"

    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    plugin_id: str = Field(
        foreign_key="plugin_definition.id", primary_key=True, ondelete="CASCADE"
    )
    enabled: bool = Field(default=False)
    auto_run_enabled: bool = Field(default=False)
    trigger_overrides: dict = Field(default_factory=dict, sa_column=Column(JSON))
    schedule_override: str | None = Field(default=None)
    #: Set (non-null) when the config circuit breaker auto-suspends this plugin
    #: after repeated configuration failures; the scheduler skips suspended plugins.
    #: Cleared by a passing config test.
    suspended_reason: str | None = Field(default=None)
    #: Consecutive `config`-kind run failures. Resets to 0 on any successful run or
    #: a passing config test; at `PLUGIN_CONFIG_FAILURE_THRESHOLD` the breaker trips.
    config_failure_streak: int = Field(default=0)
    # Action types this org opts into auto-applying (subset of low-risk types).
    auto_apply_actions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_by: str = Field(default="")


class PluginConfig(SQLModel, table=True):
    """Per-org plugin configuration. Secrets are write-only from public API."""

    __tablename__ = "plugin_config"

    organisation_id: str = Field(
        foreign_key="organisation.id", primary_key=True, ondelete="CASCADE"
    )
    plugin_id: str = Field(
        foreign_key="plugin_definition.id", primary_key=True, ondelete="CASCADE"
    )
    settings: dict = Field(default_factory=dict, sa_column=Column(JSON))
    secrets_encrypted: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str | None = Field(default=None)


class PluginRun(TimestampMixin, table=True):
    """One plugin execution attempt."""

    __tablename__ = "plugin_run"
    __table_args__ = (
        UniqueConstraint("event_id", "plugin_id", name="uq_plugin_run_event_plugin"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: str = Field(default="")
    event_type: str = Field(default="")
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    plugin_version_id: str = Field(default="")
    runner_id: str = Field(foreign_key="plugin_runner.id")
    event_object_type: str | None = Field(default=None)
    event_object_id: str | None = Field(default=None)
    permissions: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    runtime_token_hash: str | None = Field(default=None, index=True)
    runtime_token_expires_at: datetime | None = Field(default=None)
    status: str = Field(default="queued")  # queued|accepted|skipped|running|success|failure|timeout|cancelled
    skip_reason: str | None = Field(default=None)
    attempt: int = Field(default=1)
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    last_heartbeat_at: datetime | None = Field(default=None)
    progress_message: str | None = Field(default=None)
    progress_percent: int | None = Field(default=None)
    progress_updated_at: datetime | None = Field(default=None)
    error: str | None = Field(default=None)
    error_kind: str | None = Field(default=None)
    log_tail: str | None = Field(default=None)
    result_summary: dict | None = Field(default=None, sa_column=Column(JSON))
    operation_count: int = Field(default=0)
    rolled_up: bool = Field(default=False, index=True)
    created_by: str = Field(default="")


class PluginEventDelivery(TimestampMixin, table=True):
    """Tracks API-to-runner event delivery and retry state."""

    __tablename__ = "plugin_event_delivery"

    __table_args__ = (
        UniqueConstraint("event_id", "runner_id", name="uq_plugin_delivery_event_runner"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: str
    runner_id: str = Field(foreign_key="plugin_runner.id")
    envelope: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="pending")  # pending|delivered|failed|expired
    attempts: int = Field(default=0)
    next_attempt_at: datetime | None = Field(default=None)
    last_error: str | None = Field(default=None)
    delivered_at: datetime | None = Field(default=None)
    created_by: str = Field(default="")


class PluginResult(SQLModel, table=True):
    """Append-only plugin evidence for cases, alerts, observables, and tasks."""

    __tablename__ = "plugin_result"
    __table_args__ = (
        UniqueConstraint("plugin_run_id", "fingerprint", name="uq_plugin_result_run_fingerprint"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # SET NULL on run prune — results outlive their run (see retention).
    plugin_run_id: uuid.UUID | None = Field(
        default=None, foreign_key="plugin_run.id", ondelete="SET NULL"
    )
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    plugin_version_id: str = Field(default="")
    entity_type: str
    entity_id: str
    source: str = Field(default="")
    verdict: str | None = Field(default=None)
    confidence: float | None = Field(default=None)
    render_mode: str = Field(default="json")
    title: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    normalized_data: dict | None = Field(default=None, sa_column=Column(JSON))
    raw_data: dict | None = Field(default=None, sa_column=Column(JSON))
    result_metadata: dict | None = Field(default=None, sa_column=Column(JSON))
    attachments: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    fingerprint: str
    expires_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PluginRunFile(SQLModel, table=True):
    """A plugin-run-scoped blob uploaded by plugin code for result attachments."""

    __tablename__ = "plugin_run_file"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plugin_run_id: uuid.UUID | None = Field(
        default=None, foreign_key="plugin_run.id", ondelete="SET NULL"
    )
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    attachment_id: uuid.UUID = Field(foreign_key="attachment.id", ondelete="CASCADE")
    filename: str
    content_type: str = Field(default="application/octet-stream")
    size: int
    sha256: str
    purpose: str = Field(default="result_attachment")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PluginProposedAction(TimestampMixin, table=True):
    """A plugin-requested canonical mutation awaiting policy/approval."""

    __tablename__ = "plugin_proposed_action"
    # Idempotency, mirroring PluginResult.fingerprint: a redelivered event or a
    # retried run (which reuses this run row) that re-proposes the same action
    # must not create a duplicate proposal. Scoped to (plugin_run_id, fingerprint)
    # like uq_plugin_result_run_fingerprint — a genuinely different run may still
    # create its own proposal with identical content. Nullable + partial (WHERE
    # fingerprint IS NOT NULL) so legacy/direct rows without a fingerprint don't
    # collide with one another on NULL.
    __table_args__ = (
        Index(
            "uq_plugin_proposed_action_run_fingerprint",
            "plugin_run_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("fingerprint IS NOT NULL"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    plugin_run_id: uuid.UUID = Field(foreign_key="plugin_run.id", ondelete="CASCADE")
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    action_type: str
    entity_type: str
    entity_id: str
    fingerprint: str | None = Field(default=None)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # proposed | approved | rejected | applied | failed | expired | superseded
    status: str = Field(default="proposed")
    decision_reason: str | None = Field(default=None)
    decided_by: str | None = Field(default=None)
    decided_at: datetime | None = Field(default=None)
    created_by: str = Field(default="")


class PluginRunDaily(SQLModel, table=True):
    """Daily plugin run rollup retained beyond individual run rows."""

    __tablename__ = "plugin_run_daily"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id",
            "plugin_id",
            "day",
            name="uq_plugin_run_daily_org_plugin_day",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    organisation_id: str = Field(foreign_key="organisation.id", ondelete="CASCADE")
    plugin_id: str = Field(foreign_key="plugin_definition.id")
    day: datetime
    success_count: int = Field(default=0)
    failure_count: int = Field(default=0)
    timeout_count: int = Field(default=0)
    skipped_count: int = Field(default=0)
    total_duration_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = Field(default=None)
