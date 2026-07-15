"""Plugin maintenance sweep: reaper, offline detection, and daily rollup."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.plugin_runner import (
    PluginDefinition,
    PluginRun,
    PluginRunDaily,
    PluginRunner,
    PluginVersion,
)
from app.services.plugin_maintenance import (
    detect_offline_runners,
    prune_old_runs,
    prune_superseded_results,
    reap_stuck_runs,
    rollup_terminal_runs,
    run_maintenance_sweep,
)

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


async def _seed_plugin(session, org_id, *, timeout=60):
    runner = PluginRunner(id=f"runner-{uuid.uuid4().hex[:8]}")
    pdef = PluginDefinition(id=f"plugin-{uuid.uuid4().hex[:8]}", display_name="P")
    session.add(runner)
    session.add(pdef)
    await session.flush()
    version_id = f"{pdef.id}@1.0.0"
    session.add(
        PluginVersion(
            id=version_id, plugin_id=pdef.id, version="1.0.0", status="active",
            manifest={"timeout_seconds": timeout},
        )
    )
    await session.flush()
    pdef.active_version_id = version_id
    await session.flush()
    return runner, pdef, version_id


async def _make_run(session, org_id, runner, pdef, version_id, **kwargs):
    run = PluginRun(
        event_id=f"evt-{uuid.uuid4().hex[:8]}",
        event_type="observable.created",
        organisation_id=org_id,
        plugin_id=pdef.id,
        plugin_version_id=version_id,
        runner_id=runner.id,
        **kwargs,
    )
    session.add(run)
    await session.flush()
    return run


async def test_reaper_fails_runs_past_deadline(session, org_a):
    runner, pdef, version_id = await _seed_plugin(session, org_a.id, timeout=60)
    stuck = await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="running",
        started_at=NOW - timedelta(seconds=300),
        runtime_token_hash="live-token",
        runtime_token_expires_at=NOW + timedelta(seconds=600),
    )
    fresh = await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="running", started_at=NOW - timedelta(seconds=10),
    )

    reaped = await reap_stuck_runs(session, NOW)
    assert reaped == 1
    await session.refresh(stuck)
    await session.refresh(fresh)
    assert stuck.status == "failure"
    assert stuck.error_kind == "timeout"
    assert stuck.runtime_token_hash is None
    assert fresh.status == "running"


async def test_offline_detection_marks_silent_runners(session, org_a):
    runner, _, _ = await _seed_plugin(session, org_a.id)
    runner.status = "healthy"
    runner.last_heartbeat_at = NOW - timedelta(minutes=10)
    live, _, _ = await _seed_plugin(session, org_a.id)
    live.status = "healthy"
    live.last_heartbeat_at = NOW - timedelta(seconds=5)
    await session.flush()

    marked = await detect_offline_runners(session, NOW)
    assert marked == 1
    await session.refresh(runner)
    await session.refresh(live)
    assert runner.status == "offline"
    assert live.status == "healthy"


async def test_rollup_aggregates_and_marks_once(session, org_a):
    runner, pdef, version_id = await _seed_plugin(session, org_a.id)
    for _ in range(2):
        await _make_run(
            session, org_a.id, runner, pdef, version_id,
            status="success",
            started_at=NOW - timedelta(seconds=5), ended_at=NOW,
        )
    await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="failure", started_at=NOW - timedelta(seconds=3), ended_at=NOW,
    )

    rolled = await rollup_terminal_runs(session, NOW)
    assert rolled == 3

    daily = (
        await session.execute(
            PluginRunDaily.__table__.select().where(
                PluginRunDaily.__table__.c.plugin_id == pdef.id
            )
        )
    ).first()
    assert daily is not None
    assert daily.success_count == 2
    assert daily.failure_count == 1
    assert daily.total_duration_ms > 0

    # Second sweep must not double count.
    assert await rollup_terminal_runs(session, NOW) == 0


async def test_full_sweep_commits(session, org_a):
    runner, pdef, version_id = await _seed_plugin(session, org_a.id)
    await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="success", started_at=NOW - timedelta(seconds=2), ended_at=NOW,
    )
    result = await run_maintenance_sweep(session, NOW)
    assert result["reaped"] == 0
    assert result["offline"] == 0
    assert result["rolled_up"] == 1
    assert result["pruned_runs"] == 0


async def test_prune_old_runs_keeps_results(session, org_a):
    from app.models.plugin_runner import PluginResult

    runner, pdef, version_id = await _seed_plugin(session, org_a.id)
    old = await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="success",
        started_at=NOW - timedelta(days=40),
        ended_at=NOW - timedelta(days=40),
        rolled_up=True,
    )
    result = PluginResult(
        plugin_run_id=old.id, organisation_id=org_a.id, plugin_id=pdef.id,
        entity_type="observable", entity_id="obs-1", fingerprint="fp-1",
    )
    session.add(result)
    await session.flush()

    pruned = await prune_old_runs(session, NOW)
    assert pruned == 1
    # Result survives with its run link nulled out.
    await session.refresh(result)
    assert result.plugin_run_id is None


async def test_prune_old_runs_skips_recent_and_unrolled(session, org_a):
    runner, pdef, version_id = await _seed_plugin(session, org_a.id)
    # Recent terminal run — keep.
    await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="success", started_at=NOW, ended_at=NOW, rolled_up=True,
    )
    # Old but not yet rolled up — keep (stats not captured).
    await _make_run(
        session, org_a.id, runner, pdef, version_id,
        status="success",
        started_at=NOW - timedelta(days=40), ended_at=NOW - timedelta(days=40),
        rolled_up=False,
    )
    assert await prune_old_runs(session, NOW) == 0


async def test_prune_superseded_results_keeps_latest(session, org_a):
    from app.models.plugin_runner import PluginResult

    runner, pdef, version_id = await _seed_plugin(session, org_a.id)

    def _result(created, fp):
        return PluginResult(
            plugin_run_id=None, organisation_id=org_a.id, plugin_id=pdef.id,
            entity_type="observable", entity_id="obs-1", source="acme",
            fingerprint=fp, created_at=created,
        )

    old = _result(NOW - timedelta(days=120), "old")
    newer = _result(NOW - timedelta(days=100), "newer")
    session.add(old)
    session.add(newer)
    await session.flush()
    old_id, newer_id = old.id, newer.id

    pruned = await prune_superseded_results(session, NOW)
    assert pruned == 1
    assert await session.get(PluginResult, old_id) is None
    assert await session.get(PluginResult, newer_id) is not None
