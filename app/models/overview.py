"""Response models for the SOC Overview dashboard (`GET /overview`).

A single read-only aggregate that powers the landing page: KPI tiles, the
open-alert triage queue, severity/pipeline/workload breakdowns, the 24h
ingestion series and the latest observables. Everything here is derived from
existing entities (alerts, cases, tasks, observables, SLA policies) — the
overview owns no storage of its own.
"""

from datetime import datetime

from sqlmodel import SQLModel


class TrendPoint(SQLModel):
    """One label/value pair used by count breakdowns (severity, pipeline)."""

    label: str
    count: int


class KpiStats(SQLModel):
    """The four headline tiles across the top of the dashboard."""

    #: Cases currently in the Open state.
    open_cases: int
    #: Net change in open cases over the last 24h (opened − resolved).
    open_cases_delta: int

    #: Alerts ingested in the last 24h.
    new_alerts_24h: int
    #: Percent change vs the trailing 7-day daily average (rounded, signed).
    new_alerts_delta_pct: float

    #: Open cases past their severity SLA resolve target, and the critical/high
    #: split of that total.
    sla_breaches: int
    sla_breaches_critical: int
    sla_breaches_high: int

    #: Mean time to resolve (hours) for cases resolved in the last 7 days, and
    #: the delta vs the prior 7-day window.
    mttr_hours_7d: float | None
    mttr_delta_hours: float | None


class TriageAlert(SQLModel):
    """A single row in the open-alert triage queue."""

    id: int
    title: str
    severity: int
    tlp: int
    source: str
    tags: list[str] = []
    date: datetime
    breach: bool = False


class WorkloadRow(SQLModel):
    """Open-task load for one analyst (or the Unassigned bucket)."""

    name: str
    email: str | None = None
    open_tasks: int


class IngestionPoint(SQLModel):
    """One hourly bucket of the 24h ingestion series."""

    hour: datetime
    ingested: int
    promoted: int


class CaseTrendPoint(SQLModel):
    """One day in the open-vs-resolved case trend."""

    date: datetime
    opened: int
    resolved: int


class SlaCompliance(SQLModel):
    """Resolve-SLA outcomes: cases met within target vs breached (overdue-open or
    resolved-late), plus the derived compliance percentage."""

    met: int
    breached: int
    pct: float | None


class ObservableRow(SQLModel):
    """A recently created observable for the 'Latest observables' panel."""

    id: str
    type: str
    value: str
    ioc: bool
    date: datetime


class OverviewPublic(SQLModel):
    """The whole dashboard in one payload."""

    generated_at: datetime
    stats: KpiStats
    alerts_by_severity: list[TrendPoint]
    open_alerts_total: int
    triage_queue: list[TriageAlert]
    case_pipeline: list[TrendPoint]
    analyst_workload: list[WorkloadRow]
    ingestion_24h: list[IngestionPoint]
    latest_observables: list[ObservableRow]
    #: Window (days) the case trend covers — set by the `trend_days` query param.
    trend_days: int
    case_trend: list[CaseTrendPoint]
    resolution_breakdown: list[TrendPoint]
    alerts_by_source: list[TrendPoint]
    iocs_tracked: int
    cases_by_severity: list[TrendPoint]
    sla_compliance: SlaCompliance
    #: Ops health: count of dead-lettered audit-outbox rows (delivery gave up after
    #: exhausting retries). Non-zero means events are stuck and need investigation.
    #: Platform-wide (the outbox is not org-scoped), surfaced here so operators see
    #: it without a dedicated admin route.
    dead_letter_count: int = 0
