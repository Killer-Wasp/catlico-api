"""Periodic plugin maintenance: reap stuck runs, mark silent runners offline,
and roll finished runs into the daily usage table.

Runs on its own session in a background loop (see ``app.main``), and every unit
is a plain async function so tests can drive one sweep deterministically with an
injected ``now``.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.core.configs import settings
from app.models.plugin_runner import (
    PluginEventDelivery,
    PluginResult,
    PluginRun,
    PluginRunDaily,
    PluginRunner,
    PluginVersion,
)

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = ("queued", "accepted", "running", "cancelling")
_TERMINAL_STATUSES = ("success", "failure", "timeout", "cancelled", "skipped")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _timeout_for(session: AsyncSession, run: PluginRun, cache: dict) -> int:
    """Plugin's declared timeout, falling back to the configured default."""
    if run.plugin_version_id in cache:
        return cache[run.plugin_version_id]
    version = await session.get(PluginVersion, run.plugin_version_id)
    timeout = settings.PLUGIN_RUN_DEFAULT_TIMEOUT_SECONDS
    if version is not None:
        declared = (version.manifest or {}).get("timeout_seconds")
        if isinstance(declared, int) and declared > 0:
            timeout = declared
    cache[run.plugin_version_id] = timeout
    return timeout


async def reap_stuck_runs(session: AsyncSession, now: datetime) -> int:
    """Fail runs whose deadline (start/create + timeout + grace) has passed.

    Covers a crashed or partitioned runner: the run never reports terminal, so
    the API closes it out and makes it retryable. Also invalidates the token.
    """
    grace = settings.PLUGIN_RUN_REAP_GRACE_SECONDS
    runs = (
        (
            await session.execute(
                select(PluginRun).where(PluginRun.status.in_(_ACTIVE_STATUSES))
            )
        )
        .scalars()
        .all()
    )
    cache: dict = {}
    reaped = 0
    for run in runs:
        reference = _aware(run.started_at) or _aware(run.created_at)
        if reference is None:
            continue
        timeout = await _timeout_for(session, run, cache)
        deadline = reference + timedelta(seconds=timeout + grace)
        if now < deadline:
            continue
        run.status = "failure"
        run.error_kind = "timeout"
        run.error = "lost: runner did not report within deadline"
        run.ended_at = now
        run.runtime_token_hash = None
        run.runtime_token_expires_at = None
        reaped += 1
    if reaped:
        await session.flush()
    return reaped


async def detect_offline_runners(session: AsyncSession, now: datetime) -> int:
    """Mark runners offline after N missed heartbeat intervals."""
    stale_after = timedelta(
        seconds=settings.PLUGIN_HEARTBEAT_INTERVAL_SECONDS
        * settings.PLUGIN_RUNNER_OFFLINE_MISSED_HEARTBEATS
    )
    threshold = now - stale_after
    runners = (
        (
            await session.execute(
                select(PluginRunner).where(
                    PluginRunner.status != "offline",
                )
            )
        )
        .scalars()
        .all()
    )
    marked = 0
    for runner in runners:
        last = _aware(runner.last_heartbeat_at)
        if last is not None and last < threshold:
            runner.status = "offline"
            marked += 1
    if marked:
        await session.flush()
    return marked


def _day_of(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


async def rollup_terminal_runs(session: AsyncSession, now: datetime) -> int:
    """Fold not-yet-rolled terminal runs into ``PluginRunDaily`` so usage stats
    survive run pruning. Marks each run ``rolled_up`` so it is counted once."""
    runs = (
        (
            await session.execute(
                select(PluginRun).where(
                    PluginRun.status.in_(_TERMINAL_STATUSES),
                    PluginRun.rolled_up.is_(False),
                    PluginRun.ended_at.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    rolled = 0
    daily_cache: dict[tuple[str, str, datetime], PluginRunDaily] = {}
    for run in runs:
        ended = _aware(run.ended_at)
        day = _day_of(ended)
        key = (run.organisation_id, run.plugin_id, day)
        daily = daily_cache.get(key)
        if daily is None:
            daily = (
                await session.execute(
                    select(PluginRunDaily).where(
                        PluginRunDaily.organisation_id == run.organisation_id,
                        PluginRunDaily.plugin_id == run.plugin_id,
                        PluginRunDaily.day == day,
                    )
                )
            ).scalar_one_or_none()
            if daily is None:
                daily = PluginRunDaily(
                    organisation_id=run.organisation_id,
                    plugin_id=run.plugin_id,
                    day=day,
                )
                session.add(daily)
            daily_cache[key] = daily
        if run.status == "success":
            daily.success_count += 1
        elif run.status == "failure":
            daily.failure_count += 1
        elif run.status == "timeout":
            daily.timeout_count += 1
        elif run.status == "skipped":
            daily.skipped_count += 1
        elif run.status == "cancelled":
            daily.failure_count += 1
        started = _aware(run.started_at)
        if started is not None:
            daily.total_duration_ms += max(0, int((ended - started).total_seconds() * 1000))
        daily.updated_at = now
        run.rolled_up = True
        rolled += 1
    if rolled:
        await session.flush()
    return rolled


async def prune_old_runs(session: AsyncSession, now: datetime) -> int:
    """Delete terminal runs past the retention window, once rolled up (so their
    stats are captured). Results/files SET NULL rather than cascade-delete."""
    cutoff = now - timedelta(days=settings.PLUGIN_RUN_RETENTION_DAYS)
    result = await session.execute(
        delete(PluginRun).where(
            PluginRun.status.in_(_TERMINAL_STATUSES),
            PluginRun.rolled_up.is_(True),
            PluginRun.ended_at.isnot(None),
            PluginRun.ended_at < cutoff,
        )
    )
    return result.rowcount or 0


async def prune_old_deliveries(session: AsyncSession, now: datetime) -> int:
    """Delete delivered/expired event-delivery rows past their retention window."""
    cutoff = now - timedelta(days=settings.PLUGIN_DELIVERY_RETENTION_DAYS)
    result = await session.execute(
        delete(PluginEventDelivery).where(
            PluginEventDelivery.status.in_(("delivered", "expired")),
            PluginEventDelivery.created_at < cutoff,
        )
    )
    return result.rowcount or 0


async def prune_superseded_results(session: AsyncSession, now: datetime) -> int:
    """Delete results past the retention window that have been superseded by a
    newer result for the same (plugin, entity, source). The latest is never
    deleted."""
    cutoff = now - timedelta(days=settings.PLUGIN_RESULT_RETENTION_DAYS)
    newer = aliased(PluginResult)
    superseded = (
        select(newer.id)
        .where(
            and_(
                newer.plugin_id == PluginResult.plugin_id,
                newer.entity_type == PluginResult.entity_type,
                newer.entity_id == PluginResult.entity_id,
                newer.source == PluginResult.source,
                newer.created_at > PluginResult.created_at,
            )
        )
        .exists()
    )
    result = await session.execute(
        delete(PluginResult).where(PluginResult.created_at < cutoff, superseded)
    )
    return result.rowcount or 0


async def run_maintenance_sweep(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """One maintenance pass. Returns counts for observability/tests."""
    from app.services.plugin_dispatch import schedule_due_events

    now = now or datetime.now(UTC)
    reaped = await reap_stuck_runs(session, now)
    offline = await detect_offline_runners(session, now)
    rolled = await rollup_terminal_runs(session, now)
    # Rollup must precede run pruning so stats are captured before rows go.
    pruned_runs = await prune_old_runs(session, now)
    pruned_deliveries = await prune_old_deliveries(session, now)
    pruned_results = await prune_superseded_results(session, now)
    scheduled = await schedule_due_events(session, now)
    await session.commit()
    return {
        "reaped": reaped,
        "offline": offline,
        "rolled_up": rolled,
        "pruned_runs": pruned_runs,
        "pruned_deliveries": pruned_deliveries,
        "pruned_results": pruned_results,
        "scheduled": scheduled,
    }
