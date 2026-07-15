"""Aggregation queries behind the SOC Overview dashboard (`GET /overview`).

One `build_overview` entry point runs a handful of grouped counts and small
scans against the org's alerts, cases, tasks, observables and SLA policies, then
assembles the `OverviewPublic` payload. Everything is read-only and org-scoped.

Time handling: persisted timestamps are naive UTC (see `CreatedMixin`), so all
window boundaries here are naive UTC too (`_utcnow()`), and comparisons stay in
that space.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.alert import Alert, AlertStatus
from app.models.audit import AuditOutbox
from app.models.case_ import Case, CaseResolutionStatus
from app.models.case_share import CaseShare
from app.models.case_status import CaseStage, CaseStatus
from app.models.observable import Observable
from app.models.overview import (
    CaseTrendPoint,
    IngestionPoint,
    KpiStats,
    ObservableRow,
    OverviewPublic,
    SlaCompliance,
    TriageAlert,
    TrendPoint,
    WorkloadRow,
)
from app.models.sla import SlaPolicy
from app.models.tag import TaggableType
from app.models.task import Task, TaskStatus
from app.models.user import User
from app.crud.tag import tags_for_many

#: Severity number → label. Mirrors the frontend `SEV` map (1=low … 4=critical).
_SEV_LABEL = {1: "low", 2: "medium", 3: "high", 4: "critical"}
#: Alert states that count as "in the queue" — not yet promoted or ignored.
_OPEN_ALERT_STATES = (AlertStatus.new, AlertStatus.in_progress)
#: Task states that count toward an analyst's open workload.
_OPEN_TASK_STATES = (TaskStatus.waiting, TaskStatus.in_progress)
#: Case resolution → display label, in the order the breakdown panel shows them.
_RESOLUTION_LABEL = {
    CaseResolutionStatus.true_positive: "True positive",
    CaseResolutionStatus.false_positive: "False positive",
    CaseResolutionStatus.indeterminate: "Indeterminate",
    CaseResolutionStatus.other: "Other",
    CaseResolutionStatus.duplicated: "Duplicated",
}


#: The old single `open` enum state now spans two live stages.
_LIVE_STAGES = (CaseStage.open, CaseStage.in_progress)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _stage_ids(org_id: str, stages):
    """Subquery of the org's status ids whose stage is in `stages` — the
    stage-based replacement for the old `Case.status == <enum>` predicates."""
    return select(CaseStatus.id).where(
        CaseStatus.organisation_id == org_id, CaseStatus.stage.in_(list(stages))
    )


def _in_stage(org_id: str, stages):
    return Case.status_id.in_(_stage_ids(org_id, stages))


def _org_cases(stmt, org_id: str):
    """Scope a Case query to an org. Cases carry no `organisation_id`; ownership
    is expressed by a `CaseShare` row, so every case read joins through it."""
    return stmt.join(CaseShare, CaseShare.case_id == Case.id).where(
        CaseShare.organisation_id == org_id, Case.deleted_at.is_(None)
    )


async def _resolve_targets(session: AsyncSession, org_id: str) -> dict[int, int]:
    """severity → resolve-SLA seconds, for enabled policies only."""
    rows = (
        await session.execute(
            select(SlaPolicy.severity, SlaPolicy.resolve_seconds).where(
                SlaPolicy.organisation_id == org_id, SlaPolicy.enabled.is_(True)
            )
        )
    ).all()
    return {sev: secs for sev, secs in rows}


async def _kpi_stats(
    session: AsyncSession, org_id: str, now: datetime
) -> KpiStats:
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    live_alert = (Alert.organisation_id == org_id, Alert.deleted_at.is_(None))

    # --- Open cases + 24h net change (opened − resolved) ---
    open_cases = await session.scalar(
        _org_cases(select(func.count()).select_from(Case), org_id).where(
            _in_stage(org_id, _LIVE_STAGES)
        )
    )
    opened_24h = await session.scalar(
        _org_cases(select(func.count()).select_from(Case), org_id).where(
            Case.created_at >= day_ago
        )
    )
    resolved_24h = await session.scalar(
        _org_cases(select(func.count()).select_from(Case), org_id).where(
            _in_stage(org_id, [CaseStage.closed]), Case.updated_at >= day_ago
        )
    )

    # --- New alerts (24h) vs the trailing 7-day daily average ---
    new_alerts_24h = await session.scalar(
        select(func.count()).select_from(Alert).where(*live_alert, Alert.date >= day_ago)
    )
    alerts_7d = await session.scalar(
        select(func.count()).select_from(Alert).where(*live_alert, Alert.date >= week_ago)
    )
    daily_avg = (alerts_7d or 0) / 7
    delta_pct = round(((new_alerts_24h or 0) - daily_avg) / daily_avg * 100, 1) if daily_avg else 0.0

    # --- SLA breaches: open cases past their severity resolve target ---
    resolve_by_sev = await _resolve_targets(session, org_id)
    breach_total = breach_crit = breach_high = 0
    if resolve_by_sev:
        open_case_rows = (
            await session.execute(
                _org_cases(
                    select(Case.severity, Case.created_at), org_id
                ).where(_in_stage(org_id, _LIVE_STAGES))
            )
        ).all()
        for sev, created_at in open_case_rows:
            target = resolve_by_sev.get(sev)
            if target is None or created_at is None:
                continue
            # created_at and now are both tz-aware UTC.
            age = (now - created_at).total_seconds()
            if age > target:
                breach_total += 1
                if sev == 4:
                    breach_crit += 1
                elif sev == 3:
                    breach_high += 1

    return KpiStats(
        open_cases=open_cases or 0,
        open_cases_delta=(opened_24h or 0) - (resolved_24h or 0),
        new_alerts_24h=new_alerts_24h or 0,
        new_alerts_delta_pct=delta_pct,
        sla_breaches=breach_total,
        sla_breaches_critical=breach_crit,
        sla_breaches_high=breach_high,
        mttr_hours_7d=await _mttr_hours(session, org_id, week_ago, now),
        mttr_delta_hours=await _mttr_delta(session, org_id, week_ago, two_weeks_ago),
    )


async def _mttr_hours(
    session: AsyncSession, org_id: str, start: datetime, end: datetime
) -> float | None:
    """Mean hours from case open to resolve for cases resolved in [start, end)."""
    rows = (
        await session.execute(
            _org_cases(select(Case.created_at, Case.updated_at), org_id).where(
                _in_stage(org_id, [CaseStage.closed]),
                Case.updated_at >= start,
                Case.updated_at < end,
            )
        )
    ).all()
    spans = [
        (updated - created).total_seconds() / 3600
        for created, updated in rows
        if created and updated and updated >= created
    ]
    return round(sum(spans) / len(spans), 1) if spans else None


async def _mttr_delta(
    session: AsyncSession, org_id: str, week_ago: datetime, two_weeks_ago: datetime
) -> float | None:
    current = await _mttr_hours(session, org_id, week_ago, _utcnow())
    prior = await _mttr_hours(session, org_id, two_weeks_ago, week_ago)
    if current is None or prior is None:
        return None
    return round(current - prior, 1)


async def _alerts_by_severity(
    session: AsyncSession, org_id: str
) -> tuple[list[TrendPoint], int]:
    rows = (
        await session.execute(
            select(Alert.severity, func.count())
            .where(
                Alert.organisation_id == org_id,
                Alert.deleted_at.is_(None),
                Alert.status.in_(_OPEN_ALERT_STATES),
            )
            .group_by(Alert.severity)
        )
    ).all()
    counts = {sev: n for sev, n in rows}
    # Highest severity first, matching the dashboard's severity panel.
    points = [
        TrendPoint(label=_SEV_LABEL[sev], count=counts.get(sev, 0))
        for sev in (4, 3, 2, 1)
    ]
    return points, sum(counts.values())


async def _triage_queue(
    session: AsyncSession, org_id: str, now: datetime, limit: int = 10
) -> list[TriageAlert]:
    alerts = (
        (
            await session.execute(
                select(Alert)
                .where(
                    Alert.organisation_id == org_id,
                    Alert.deleted_at.is_(None),
                    Alert.status.in_(_OPEN_ALERT_STATES),
                )
                .order_by(Alert.severity.desc(), Alert.date.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    tags_map = await tags_for_many(
        session, TaggableType.alert, [str(a.id) for a in alerts]
    )
    return [
        TriageAlert(
            id=a.id,
            title=a.title,
            severity=a.severity,
            tlp=a.tlp,
            source=a.source,
            tags=tags_map.get(str(a.id), []),
            date=a.date,
        )
        for a in alerts
    ]


async def _case_pipeline(session: AsyncSession, org_id: str) -> list[TrendPoint]:
    """Case funnel, grouped by the status *stage*. Statuses now carry an explicit
    open vs in_progress stage, so the funnel reads them directly instead of
    splitting a single open state by assignment."""
    rows = (
        await session.execute(
            _org_cases(
                select(CaseStatus.stage, func.count()).select_from(Case), org_id
            )
            .join(CaseStatus, CaseStatus.id == Case.status_id)
            .group_by(CaseStatus.stage)
        )
    ).all()
    counts = {stage: n for stage, n in rows}
    return [
        TrendPoint(label="Open", count=counts.get(CaseStage.open, 0)),
        TrendPoint(label="In progress", count=counts.get(CaseStage.in_progress, 0)),
        TrendPoint(label="Resolved", count=counts.get(CaseStage.closed, 0)),
        TrendPoint(label="Duplicated", count=counts.get(CaseStage.duplicated, 0)),
    ]


async def _analyst_workload(
    session: AsyncSession, org_id: str, limit: int = 6
) -> list[WorkloadRow]:
    rows = (
        await session.execute(
            select(Task.assignee_id, func.count())
            .where(
                Task.organisation_id == org_id,
                Task.deleted_at.is_(None),
                Task.status.in_(_OPEN_TASK_STATES),
            )
            .group_by(Task.assignee_id)
        )
    ).all()

    assigned = [(uid, n) for uid, n in rows if uid is not None]
    unassigned = sum(n for uid, n in rows if uid is None)

    names = {}
    if assigned:
        user_rows = (
            await session.execute(
                select(User.id, User.first_name, User.last_name, User.email).where(
                    User.id.in_([uid for uid, _ in assigned])
                )
            )
        ).all()
        names = {uid: (f"{fn} {ln}", email) for uid, fn, ln, email in user_rows}

    workload = [
        WorkloadRow(
            name=names.get(uid, ("Unknown", None))[0],
            email=names.get(uid, ("Unknown", None))[1],
            open_tasks=n,
        )
        for uid, n in assigned
    ]
    workload.sort(key=lambda r: r.open_tasks, reverse=True)
    workload = workload[:limit]
    if unassigned:
        workload.append(WorkloadRow(name="Unassigned", email=None, open_tasks=unassigned))
    return workload


async def _ingestion_24h(
    session: AsyncSession, org_id: str, now: datetime
) -> list[IngestionPoint]:
    day_ago = now - timedelta(hours=24)
    rows = (
        await session.execute(
            select(Alert.date, Alert.case_id).where(
                Alert.organisation_id == org_id,
                Alert.deleted_at.is_(None),
                Alert.date >= day_ago,
            )
        )
    ).all()

    # 24 hourly buckets ending at the current hour.
    start_hour = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    ingested: dict[datetime, int] = defaultdict(int)
    promoted: dict[datetime, int] = defaultdict(int)
    for date, case_id in rows:
        bucket = date.replace(minute=0, second=0, microsecond=0)
        if bucket < start_hour:
            continue
        ingested[bucket] += 1
        if case_id is not None:
            promoted[bucket] += 1

    return [
        IngestionPoint(
            hour=(hour := start_hour + timedelta(hours=i)),
            ingested=ingested.get(hour, 0),
            promoted=promoted.get(hour, 0),
        )
        for i in range(24)
    ]


async def _latest_observables(
    session: AsyncSession, org_id: str, limit: int = 7
) -> list[ObservableRow]:
    observables = (
        (
            await session.execute(
                select(Observable)
                .where(
                    Observable.organisation_id == org_id,
                    Observable.deleted_at.is_(None),
                )
                .order_by(Observable.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        ObservableRow(
            id=str(o.id),
            type=o.observable_type,
            value=o.data,
            ioc=o.ioc,
            date=o.created_at,
        )
        for o in observables
    ]


async def _case_trend(
    session: AsyncSession, org_id: str, now: datetime, days: int = 14
) -> list[CaseTrendPoint]:
    """Daily opened-vs-resolved case counts over the trailing `days` window —
    the backlog trend. `opened` buckets by `created_at`; `resolved` buckets
    resolved cases by `updated_at` (the resolve time proxy)."""
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days - 1
    )
    opened_rows = (
        await session.execute(
            _org_cases(select(Case.created_at), org_id).where(
                Case.created_at >= start_day
            )
        )
    ).all()
    resolved_rows = (
        await session.execute(
            _org_cases(select(Case.updated_at), org_id).where(
                _in_stage(org_id, [CaseStage.closed]), Case.updated_at >= start_day
            )
        )
    ).all()

    def _day(dt: datetime) -> datetime:
        # Truncate to midnight; stays tz-aware UTC so day buckets match the
        # aware `start_day`/`day` keys.
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    opened: dict[datetime, int] = defaultdict(int)
    resolved: dict[datetime, int] = defaultdict(int)
    for (created,) in opened_rows:
        opened[_day(created)] += 1
    for (updated,) in resolved_rows:
        if updated is not None:
            resolved[_day(updated)] += 1

    return [
        CaseTrendPoint(
            date=(day := start_day + timedelta(days=i)),
            opened=opened.get(day, 0),
            resolved=resolved.get(day, 0),
        )
        for i in range(days)
    ]


async def _resolution_breakdown(
    session: AsyncSession, org_id: str
) -> list[TrendPoint]:
    """Resolved cases grouped by resolution status — the true/false-positive
    quality view. Only statuses with at least one case are returned."""
    rows = (
        await session.execute(
            _org_cases(
                select(Case.resolution_status, func.count()), org_id
            )
            .where(_in_stage(org_id, [CaseStage.closed]))
            .group_by(Case.resolution_status)
        )
    ).all()
    counts = {status: n for status, n in rows}
    points = [
        TrendPoint(label=label, count=counts[status])
        for status, label in _RESOLUTION_LABEL.items()
        if counts.get(status)
    ]
    # Resolved cases with no recorded disposition still count somewhere.
    if counts.get(None):
        points.append(TrendPoint(label="Unset", count=counts[None]))
    return points


async def _alerts_by_source(
    session: AsyncSession, org_id: str, limit: int = 6
) -> list[TrendPoint]:
    """Alert volume per connected feed (Defender XDR, Splunk, …), busiest first."""
    rows = (
        await session.execute(
            select(Alert.source, func.count())
            .where(Alert.organisation_id == org_id, Alert.deleted_at.is_(None))
            .group_by(Alert.source)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [TrendPoint(label=source, count=n) for source, n in rows]


async def _iocs_tracked(session: AsyncSession, org_id: str) -> int:
    """Count of observables flagged as IOCs across the org."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Observable)
            .where(
                Observable.organisation_id == org_id,
                Observable.deleted_at.is_(None),
                Observable.ioc.is_(True),
            )
        )
    ) or 0


async def _cases_by_severity(session: AsyncSession, org_id: str) -> list[TrendPoint]:
    """All org cases grouped by severity, highest first."""
    rows = (
        await session.execute(
            _org_cases(select(Case.severity, func.count()), org_id).group_by(
                Case.severity
            )
        )
    ).all()
    counts = {sev: n for sev, n in rows}
    return [
        TrendPoint(label=_SEV_LABEL[sev], count=counts.get(sev, 0))
        for sev in (4, 3, 2, 1)
    ]


async def _sla_compliance(
    session: AsyncSession, org_id: str, now: datetime
) -> SlaCompliance:
    """Resolve-SLA outcomes: cases resolved within target (met) vs breached
    (currently overdue-open, or resolved after target). Percentage over the sum."""
    targets = await _resolve_targets(session, org_id)
    if not targets:
        return SlaCompliance(met=0, breached=0, pct=None)

    week_ago = now - timedelta(days=7)
    met = breached = 0

    # Currently-open cases past their target are live breaches.
    open_rows = (
        await session.execute(
            _org_cases(select(Case.severity, Case.created_at), org_id).where(
                _in_stage(org_id, _LIVE_STAGES)
            )
        )
    ).all()
    for sev, created_at in open_rows:
        target = targets.get(sev)
        if target is None or created_at is None:
            continue
        if (now - created_at).total_seconds() > target:
            breached += 1

    # Cases resolved in the last 7 days: met if within target, else breached.
    resolved_rows = (
        await session.execute(
            _org_cases(
                select(Case.severity, Case.created_at, Case.updated_at), org_id
            ).where(_in_stage(org_id, [CaseStage.closed]), Case.updated_at >= week_ago)
        )
    ).all()
    for sev, created_at, updated_at in resolved_rows:
        target = targets.get(sev)
        if target is None or created_at is None or updated_at is None:
            continue
        duration = (updated_at - created_at).total_seconds()
        if duration <= target:
            met += 1
        else:
            breached += 1

    total = met + breached
    pct = round(met / total * 100, 1) if total else None
    return SlaCompliance(met=met, breached=breached, pct=pct)


async def _dead_letter_count(session: AsyncSession) -> int:
    """Platform-wide count of dead-lettered outbox rows (a stuck-delivery signal).
    Not org-scoped — the outbox has no organisation column — but cheap to surface
    on the org dashboard so operators notice stuck events. Index-backed: the
    partial index ``ix_audit_outbox_dead_lettered_at`` (WHERE dead_lettered_at IS
    NOT NULL) keeps this COUNT off a seq scan even though it runs on every build."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(AuditOutbox)
            .where(AuditOutbox.dead_lettered_at.isnot(None))
        )
    ) or 0


async def build_overview(
    session: AsyncSession, org_id: str, *, trend_days: int = 14
) -> OverviewPublic:
    now = _utcnow()
    alerts_by_severity, open_alerts_total = await _alerts_by_severity(session, org_id)
    return OverviewPublic(
        generated_at=now,
        stats=await _kpi_stats(session, org_id, now),
        alerts_by_severity=alerts_by_severity,
        open_alerts_total=open_alerts_total,
        triage_queue=await _triage_queue(session, org_id, now),
        case_pipeline=await _case_pipeline(session, org_id),
        analyst_workload=await _analyst_workload(session, org_id),
        ingestion_24h=await _ingestion_24h(session, org_id, now),
        latest_observables=await _latest_observables(session, org_id),
        trend_days=trend_days,
        case_trend=await _case_trend(session, org_id, now, days=trend_days),
        resolution_breakdown=await _resolution_breakdown(session, org_id),
        alerts_by_source=await _alerts_by_source(session, org_id),
        iocs_tracked=await _iocs_tracked(session, org_id),
        cases_by_severity=await _cases_by_severity(session, org_id),
        sla_compliance=await _sla_compliance(session, org_id, now),
        dead_letter_count=await _dead_letter_count(session),
    )
