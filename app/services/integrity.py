"""Minimal report-only integrity checks (Phase 6 §6.6).

Three narrow checks over the small drift surface — no general framework. Each is a
plain async function returning a report dict; the ``GET /admin/integrity`` route runs
them and logs warnings. None mutate: the seq check's fix is manual/restore-time, and
the rollup check reports (recompute-and-compare) rather than auto-fixing here.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.models.attachment import AttachmentLink
from app.models.case_ import Case
from app.models.log import Log
from app.models.plugin_runner import PluginRun, PluginRunDaily
from app.models.task import Task
from app.services.plugin_maintenance import (
    _TERMINAL_STATUSES,
    _aware,
    _day_of,
)

logger = logging.getLogger(__name__)


async def check_rollup_drift(session: AsyncSession, now: datetime) -> dict:
    """Recompute ``PluginRunDaily`` counters from the still-present rolled-up runs and
    compare against the stored rollup, surfacing the unlocked ``rolled_up`` double-count
    race (`plugin_maintenance.rollup_terminal_runs`).

    Only days newer than the run-retention window are checked: older days may have had
    their runs pruned, so a recompute there would be incomplete and a mismatch spurious.
    Report-only — the counters are derivable within retention, but auto-fix is left to a
    deliberate follow-up."""
    retention_cutoff = _day_of(now - timedelta(days=settings.PLUGIN_RUN_RETENTION_DAYS))
    runs = (
        (
            await session.execute(
                select(PluginRun).where(
                    PluginRun.rolled_up.is_(True),
                    PluginRun.status.in_(_TERMINAL_STATUSES),
                    PluginRun.ended_at.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )
    expected: dict[tuple[str, str, datetime], dict[str, int]] = {}
    for run in runs:
        day = _day_of(_aware(run.ended_at))
        key = (run.organisation_id, run.plugin_id, day)
        e = expected.setdefault(
            key, {"success": 0, "failure": 0, "timeout": 0, "skipped": 0}
        )
        if run.status == "success":
            e["success"] += 1
        elif run.status == "failure":
            e["failure"] += 1
        elif run.status == "timeout":
            e["timeout"] += 1
        elif run.status == "skipped":
            e["skipped"] += 1
        elif run.status == "cancelled":
            e["failure"] += 1

    dailies = (await session.execute(select(PluginRunDaily))).scalars().all()
    checked = 0
    mismatches: list[dict] = []
    for d in dailies:
        if _aware(d.day) < retention_cutoff:
            continue  # runs possibly pruned — can't recompute reliably
        checked += 1
        e = expected.get(
            (d.organisation_id, d.plugin_id, _aware(d.day)),
            {"success": 0, "failure": 0, "timeout": 0, "skipped": 0},
        )
        stored = (
            d.success_count,
            d.failure_count,
            d.timeout_count,
            d.skipped_count,
        )
        recomputed = (e["success"], e["failure"], e["timeout"], e["skipped"])
        if stored != recomputed:
            mismatches.append(
                {
                    "organisation_id": d.organisation_id,
                    "plugin_id": d.plugin_id,
                    "day": d.day.isoformat(),
                    "stored": stored,
                    "recomputed": recomputed,
                }
            )
    return {
        "checked": checked,
        "mismatches": len(mismatches),
        "details": mismatches[:20],
    }


async def check_seq_high_water(session: AsyncSession) -> dict:
    """Verify each per-scope counter still sits strictly above the highest child id it
    has handed out — the invariant a bad restore can break (`crud/_seq.py`).

    Report-only: the fix is a manual counter bump at restore time, never automatic."""
    violations: list[dict] = []

    # Case.next_task_seq > max(task.id)
    for case_id, nxt, mx in (
        await session.execute(
            select(Case.id, Case.next_task_seq, func.max(Task.id))
            .join(Task, Task.case_id == Case.id)
            .group_by(Case.id, Case.next_task_seq)
        )
    ).all():
        if mx is not None and nxt <= mx:
            violations.append(
                {"scope": "case.next_task_seq", "case_id": case_id, "next": nxt, "max": mx}
            )

    # Case.next_attachment_seq > max(attachment_link.id)
    for case_id, nxt, mx in (
        await session.execute(
            select(Case.id, Case.next_attachment_seq, func.max(AttachmentLink.id))
            .join(AttachmentLink, AttachmentLink.case_id == Case.id)
            .group_by(Case.id, Case.next_attachment_seq)
        )
    ).all():
        if mx is not None and nxt <= mx:
            violations.append(
                {
                    "scope": "case.next_attachment_seq",
                    "case_id": case_id,
                    "next": nxt,
                    "max": mx,
                }
            )

    # Task.next_log_seq > max(log.id) per (case, task)
    for case_id, task_id, nxt, mx in (
        await session.execute(
            select(Task.case_id, Task.id, Task.next_log_seq, func.max(Log.id))
            .join(Log, (Log.case_id == Task.case_id) & (Log.task_id == Task.id))
            .group_by(Task.case_id, Task.id, Task.next_log_seq)
        )
    ).all():
        if mx is not None and nxt <= mx:
            violations.append(
                {
                    "scope": "task.next_log_seq",
                    "case_id": case_id,
                    "task_id": task_id,
                    "next": nxt,
                    "max": mx,
                }
            )

    return {"violations": len(violations), "details": violations[:20]}
