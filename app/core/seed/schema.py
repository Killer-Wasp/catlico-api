"""Pydantic schema for JSON seed profiles.

A profile is a directory of per-entity JSON files under
``app/core/seed_data/<profile>/`` (e.g. ``demo/``, ``dev/``). Each file holds a
single top-level array of one entity type. These models validate that content
on load, so a malformed profile fails fast at startup with a clear error rather
than a mid-seed DB exception.

Timestamps are stored as *offset strings* relative to a single ``now`` captured
at seed time — e.g. ``"-4h"`` (four hours ago), ``"-3d"``, ``"+2h30m"``. This
keeps the data date-independent: a freshly seeded DB always looks "live". Use
:func:`parse_offset` to resolve one and :func:`format_offset` to author one.
"""

from __future__ import annotations

import re
from datetime import timedelta

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Offset DSL: "[-+]<n>d<n>h<n>m" — any subset of units, signed. Applied to the
# seed-time `now` (negative = in the past). Units may be fractional.
# ---------------------------------------------------------------------------

_OFFSET_RE = re.compile(
    r"^([+-]?)"
    r"(?:(\d+(?:\.\d+)?)d)?"
    r"(?:(\d+(?:\.\d+)?)h)?"
    r"(?:(\d+(?:\.\d+)?)m)?$"
)


def parse_offset(value: str) -> timedelta:
    """Resolve an offset string like ``"-4h"`` / ``"+2h30m"`` to a timedelta."""
    text = value.strip()
    match = _OFFSET_RE.match(text)
    if not match or text in ("", "+", "-"):
        raise ValueError(f"Invalid time offset: {value!r} (expected e.g. '-4h', '+2d3h')")
    sign = -1.0 if match.group(1) == "-" else 1.0
    days = float(match.group(2) or 0)
    hours = float(match.group(3) or 0)
    minutes = float(match.group(4) or 0)
    if not (match.group(2) or match.group(3) or match.group(4)):
        raise ValueError(f"Empty time offset: {value!r}")
    return sign * timedelta(days=days, hours=hours, minutes=minutes)


def format_offset(delta: timedelta) -> str:
    """Render a timedelta (relative to now) as a canonical offset string. Inverse
    of :func:`parse_offset`; used by the profile generator, not the loader."""
    total_minutes = delta.total_seconds() / 60
    sign = "-" if total_minutes < 0 else "+"
    total_minutes = abs(total_minutes)
    days = int(total_minutes // 1440)
    total_minutes -= days * 1440
    hours = int(total_minutes // 60)
    minutes = total_minutes - hours * 60
    parts = ""
    if days:
        parts += f"{days}d"
    if hours:
        parts += f"{hours}h"
    if minutes or not parts:
        parts += f"{minutes:g}m"
    return sign + parts


# ---------------------------------------------------------------------------
# Entity models. Field names mirror the corresponding *Create models so the
# loader can map them with minimal translation.
# ---------------------------------------------------------------------------


class SeedUser(BaseModel):
    email: str
    first_name: str
    last_name: str
    password: str = "changeme"
    role: str = "org-admin"
    #: The primary actor — default creator of case-scoped entities. Exactly one
    #: user per profile should set this true.
    primary: bool = False


class SeedCustomFieldDefinition(BaseModel):
    name: str
    display_name: str
    field_type: str  # matches CustomFieldType (string | integer | ...)


class SeedOrganisation(BaseModel):
    id: str
    name: str
    description: str
    users: list[SeedUser] = Field(default_factory=list)
    custom_field_definitions: list[SeedCustomFieldDefinition] = Field(
        default_factory=list
    )


class SeedAlert(BaseModel):
    ref: str  # source_ref, also the linking key for cases
    source: str
    type: str
    title: str
    description: str
    severity: int
    tlp: int = 0
    date: str | None = None  # offset string
    tags: list[str] = Field(default_factory=list)


class SeedObservable(BaseModel):
    observable_type: str
    data: str
    message: str = ""
    tlp: int = 2
    ioc: bool = False
    sighted: bool = False


class SeedComment(BaseModel):
    message: str


class SeedCase(BaseModel):
    ref: str  # stable id used by tasks/logs to reference this case
    title: str
    description: str = ""
    severity: int
    tlp: int = 2
    pap: int = 2
    assignee: str | None = None  # user email, or null for unassigned
    summary: str | None = None
    status: str = "open"  # open | resolved
    resolution: str | None = None  # CaseResolutionStatus name when resolved
    #: Timing offsets (relative to now). `created` backdates created_at; `resolved`
    #: stamps end_date/updated_at for the trend + MTTR charts.
    start: str | None = None
    created: str | None = None
    resolved: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, str | int] = Field(default_factory=dict)
    observables: list[SeedObservable] = Field(default_factory=list)
    comments: list[SeedComment] = Field(default_factory=list)
    link_alert: str | None = None  # alert ref to promote into this case


class SeedLog(BaseModel):
    message: str
    occurred: str | None = None  # offset string


class SeedTask(BaseModel):
    case: str  # case ref
    title: str
    group: str
    description: str = ""
    assignee: str | None = None
    order: int
    status: str = "waiting"  # TaskStatus name
    start: str | None = None
    due: str | None = None
    logs: list[SeedLog] = Field(default_factory=list)


class SeedKnowledgeBasePage(BaseModel):
    title: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    content: str = ""


class SeedSlaPolicy(BaseModel):
    severity: int
    ack_seconds: int
    resolve_seconds: int
    escalation_target: str | None = None


class SeedDashboard(BaseModel):
    name: str
    description: str = ""
    shared: bool = False
    owner: str = "primary"  # primary | superadmin
    widgets: list[dict[str, str]] = Field(default_factory=list)


class SeedProfile(BaseModel):
    """A fully-parsed profile: the organisation plus every entity list."""

    org: SeedOrganisation
    alerts: list[SeedAlert] = Field(default_factory=list)
    cases: list[SeedCase] = Field(default_factory=list)
    tasks: list[SeedTask] = Field(default_factory=list)
    pages: list[SeedKnowledgeBasePage] = Field(default_factory=list)
    policies: list[SeedSlaPolicy] = Field(default_factory=list)
    dashboards: list[SeedDashboard] = Field(default_factory=list)


# Map of profile filename -> (json top-level key, SeedProfile attribute, item
# model). The loader reads each file, pulls the array under `key`, validates
# every item, and assigns the list to `attr`.
PROFILE_FILES: dict[str, tuple[str, str, type[BaseModel]]] = {
    "alerts.json": ("alerts", "alerts", SeedAlert),
    "cases.json": ("cases", "cases", SeedCase),
    "tasks.json": ("tasks", "tasks", SeedTask),
    "knowledge_base.json": ("pages", "pages", SeedKnowledgeBasePage),
    "sla.json": ("policies", "policies", SeedSlaPolicy),
    "dashboards.json": ("dashboards", "dashboards", SeedDashboard),
}
