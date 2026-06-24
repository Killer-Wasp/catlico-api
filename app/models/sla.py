from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.common import TimestampMixin


class SlaPolicy(TimestampMixin, table=True):
    """An org-scoped acknowledge/resolve target for one severity level.

    One row per (organisation, severity); severity reuses the app's 1–4 scale
    (1=low … 4=critical). Durations are stored as seconds — the UI formats them
    (`15m`/`4h`/`2d`). `escalation_target` is free text (e.g. "On-call lead")."""

    __tablename__ = "sla_policy"
    __table_args__ = (
        UniqueConstraint("organisation_id", "severity", name="uq_sla_policy_org_sev"),
    )

    id: int | None = Field(default=None, primary_key=True)
    organisation_id: str = Field(
        foreign_key="organisation.id", index=True, ondelete="CASCADE"
    )
    severity: int
    ack_seconds: int
    resolve_seconds: int
    escalation_target: str = Field(default="")
    enabled: bool = Field(default=True)


class SlaPolicyUpsert(SQLModel):
    severity: int
    ack_seconds: int
    resolve_seconds: int
    escalation_target: str = ""
    enabled: bool = True


class SlaPolicyPublic(SQLModel):
    id: int
    severity: int
    ack_seconds: int
    resolve_seconds: int
    escalation_target: str
    enabled: bool
    organisation_id: str
    created_at: datetime
    updated_at: datetime | None
