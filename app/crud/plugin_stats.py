"""Plugin usage-stats aggregation.

Reads the daily rollup (``PluginRunDaily``, which survives run pruning) plus any
terminal runs not yet rolled up, so a window's numbers are complete without
double counting. Runner stats read live runs by ``runner_id`` (the rollup has no
runner dimension).
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin_runner import PluginRun, PluginRunDaily

_WINDOWS = {"7d": 7, "30d": 30, "90d": 90}
_TERMINAL = ("success", "failure", "timeout", "cancelled", "skipped")


def window_days(window: str) -> int:
    if window not in _WINDOWS:
        raise ValueError(f"Unsupported window: {window}")
    return _WINDOWS[window]


def _empty() -> dict:
    return {
        "success": 0,
        "failure": 0,
        "timeout": 0,
        "skipped": 0,
        "total_duration_ms": 0,
    }


def _finalize(counts: dict, window: str) -> dict:
    executed = counts["success"] + counts["failure"] + counts["timeout"]
    total = executed + counts["skipped"]
    success_rate = counts["success"] / executed if executed else None
    avg_duration_ms = counts["total_duration_ms"] / executed if executed else None
    return {
        "window": window,
        "success": counts["success"],
        "failure": counts["failure"],
        "timeout": counts["timeout"],
        "skipped": counts["skipped"],
        "total": total,
        "success_rate": success_rate,
        "avg_duration_ms": avg_duration_ms,
    }


async def plugin_stats(
    session: AsyncSession,
    organisation_id: str,
    plugin_id: str,
    window: str,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    days = window_days(window)
    since = now - timedelta(days=days)
    since_day = datetime(since.year, since.month, since.day, tzinfo=UTC)
    counts = _empty()

    daily_rows = (
        (
            await session.execute(
                select(PluginRunDaily).where(
                    PluginRunDaily.organisation_id == organisation_id,
                    PluginRunDaily.plugin_id == plugin_id,
                    PluginRunDaily.day >= since_day,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in daily_rows:
        counts["success"] += row.success_count
        counts["failure"] += row.failure_count
        counts["timeout"] += row.timeout_count
        counts["skipped"] += row.skipped_count
        counts["total_duration_ms"] += row.total_duration_ms

    # Terminal runs not yet folded into the rollup.
    live = (
        (
            await session.execute(
                select(PluginRun).where(
                    PluginRun.organisation_id == organisation_id,
                    PluginRun.plugin_id == plugin_id,
                    PluginRun.rolled_up.is_(False),
                    PluginRun.status.in_(_TERMINAL),
                    PluginRun.ended_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    _absorb_runs(counts, live)
    result = _finalize(counts, window)
    result["plugin_id"] = plugin_id
    return result


async def runner_stats(
    session: AsyncSession,
    runner_id: str,
    window: str,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    days = window_days(window)
    since = now - timedelta(days=days)
    counts = _empty()
    runs = (
        (
            await session.execute(
                select(PluginRun).where(
                    PluginRun.runner_id == runner_id,
                    PluginRun.status.in_(_TERMINAL),
                    PluginRun.ended_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    _absorb_runs(counts, runs)
    result = _finalize(counts, window)
    result["runner_id"] = runner_id
    return result


def _absorb_runs(counts: dict, runs) -> None:
    for run in runs:
        if run.status == "cancelled":
            counts["failure"] += 1
        elif run.status in counts:
            counts[run.status] += 1
        started = run.started_at
        ended = run.ended_at
        if started is not None and ended is not None:
            s = started if started.tzinfo else started.replace(tzinfo=UTC)
            e = ended if ended.tzinfo else ended.replace(tzinfo=UTC)
            counts["total_duration_ms"] += max(0, int((e - s).total_seconds() * 1000))
