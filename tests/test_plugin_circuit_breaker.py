"""Config circuit breaker: auto-suspend on repeated config failures, clear on a
passing config test (plugin_circuit_breaker)."""
import uuid

from app.models.plugin_runner import (
    OrgPlugin,
    PluginDefinition,
    PluginRun,
    PluginRunner,
    PluginVersion,
)
from app.services import plugin_circuit_breaker as cb


async def _seed(session, org_id, *, enabled=True):
    runner = PluginRunner(id=f"runner-{uuid.uuid4().hex[:8]}", enrollment_state="enrolled")
    pdef = PluginDefinition(id=f"plugin-{uuid.uuid4().hex[:8]}", display_name="P")
    session.add(runner)
    session.add(pdef)
    await session.flush()
    version_id = f"{pdef.id}@1.0.0"
    session.add(PluginVersion(id=version_id, plugin_id=pdef.id, version="1.0.0", status="active"))
    session.add(OrgPlugin(organisation_id=org_id, plugin_id=pdef.id, enabled=enabled))
    await session.flush()
    return runner, pdef, version_id


async def _make_run(session, org_id, runner, pdef, version_id, *, status, error_kind=None):
    run = PluginRun(
        event_id=f"evt-{uuid.uuid4().hex[:8]}",
        event_type="observable.created",
        organisation_id=org_id,
        plugin_id=pdef.id,
        plugin_version_id=version_id,
        runner_id=runner.id,
        status=status,
        error_kind=error_kind,
    )
    session.add(run)
    await session.flush()
    return run


async def _org_plugin(session, org_id, plugin_id) -> OrgPlugin:
    return await session.get(OrgPlugin, (org_id, plugin_id))


async def test_trips_after_threshold_consecutive_config_failures(session, org_a):
    runner, pdef, vid = await _seed(session, org_a.id)

    for i in range(1, 3):  # first two failures: streak climbs, not yet suspended
        run = await _make_run(
            session, org_a.id, runner, pdef, vid, status="failure", error_kind="config"
        )
        await cb.record_run_outcome(session, run, threshold=3)
        op = await _org_plugin(session, org_a.id, pdef.id)
        assert op.config_failure_streak == i
        assert op.suspended_reason is None

    # Third consecutive config failure trips the breaker.
    run = await _make_run(
        session, org_a.id, runner, pdef, vid, status="failure", error_kind="config"
    )
    await cb.record_run_outcome(session, run, threshold=3)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.config_failure_streak == 3
    assert op.suspended_reason is not None
    assert "configuration failures" in op.suspended_reason


async def test_success_resets_the_streak(session, org_a):
    runner, pdef, vid = await _seed(session, org_a.id)
    for _ in range(2):
        run = await _make_run(
            session, org_a.id, runner, pdef, vid, status="failure", error_kind="config"
        )
        await cb.record_run_outcome(session, run, threshold=3)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.config_failure_streak == 2

    success = await _make_run(session, org_a.id, runner, pdef, vid, status="success")
    await cb.record_run_outcome(session, success, threshold=3)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.config_failure_streak == 0
    assert op.suspended_reason is None


async def test_non_config_failures_do_not_trip_the_breaker(session, org_a):
    runner, pdef, vid = await _seed(session, org_a.id)
    for kind in ("timeout", "transient", "unknown"):
        run = await _make_run(
            session, org_a.id, runner, pdef, vid, status="failure", error_kind=kind
        )
        await cb.record_run_outcome(session, run, threshold=3)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.config_failure_streak == 0
    assert op.suspended_reason is None


async def test_clear_suspension_resets_streak_and_reason(session, org_a):
    runner, pdef, vid = await _seed(session, org_a.id)
    for _ in range(3):
        run = await _make_run(
            session, org_a.id, runner, pdef, vid, status="failure", error_kind="config"
        )
        await cb.record_run_outcome(session, run, threshold=3)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.suspended_reason is not None

    await cb.clear_suspension(session, org_a.id, pdef.id)
    op = await _org_plugin(session, org_a.id, pdef.id)
    assert op.config_failure_streak == 0
    assert op.suspended_reason is None


async def test_no_org_plugin_row_is_a_noop(session, org_a):
    # A run for a plugin the org never enabled has no OrgPlugin row.
    runner = PluginRunner(id=f"runner-{uuid.uuid4().hex[:8]}", enrollment_state="enrolled")
    pdef = PluginDefinition(id=f"plugin-{uuid.uuid4().hex[:8]}", display_name="P")
    session.add(runner)
    session.add(pdef)
    await session.flush()
    vid = f"{pdef.id}@1.0.0"
    session.add(PluginVersion(id=vid, plugin_id=pdef.id, version="1.0.0", status="active"))
    await session.flush()
    run = await _make_run(
        session, org_a.id, runner, pdef, vid, status="failure", error_kind="config"
    )
    # Must not raise.
    await cb.record_run_outcome(session, run, threshold=3)
    await cb.clear_suspension(session, org_a.id, pdef.id)
