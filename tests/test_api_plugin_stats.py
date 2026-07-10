"""Plugin/runner usage stats — rollup + live aggregation and the public endpoints."""
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.crud.plugin_stats import plugin_stats, runner_stats
from app.services.plugin_maintenance import rollup_terminal_runs

from tests.test_plugin_maintenance import _make_run, _seed_plugin

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


def _user_h(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def test_plugin_stats_combine_rollup_and_live(session, org_a):
    runner, pdef, vid = await _seed_plugin(session, org_a.id)
    # Two rolled-up successes.
    for _ in range(2):
        await _make_run(
            session, org_a.id, runner, pdef, vid,
            status="success", started_at=NOW - timedelta(seconds=4), ended_at=NOW,
        )
    await rollup_terminal_runs(session, NOW)
    # One live (not-yet-rolled) failure.
    await _make_run(
        session, org_a.id, runner, pdef, vid,
        status="failure", started_at=NOW - timedelta(seconds=2),
        ended_at=NOW - timedelta(seconds=1),
    )

    stats = await plugin_stats(session, org_a.id, pdef.id, "90d", now=NOW)
    assert stats["success"] == 2
    assert stats["failure"] == 1
    assert stats["total"] == 3
    assert abs(stats["success_rate"] - (2 / 3)) < 1e-9
    assert stats["avg_duration_ms"] > 0


async def test_runner_stats_counts_by_runner(session, org_a):
    runner, pdef, vid = await _seed_plugin(session, org_a.id)
    await _make_run(
        session, org_a.id, runner, pdef, vid,
        status="success", started_at=NOW - timedelta(seconds=3), ended_at=NOW,
    )
    await _make_run(
        session, org_a.id, runner, pdef, vid,
        status="timeout", started_at=NOW - timedelta(seconds=3), ended_at=NOW,
    )
    stats = await runner_stats(session, runner.id, "30d", now=NOW)
    assert stats["success"] == 1
    assert stats["timeout"] == 1
    assert stats["runner_id"] == runner.id


async def test_plugin_stats_endpoint(
    client: AsyncClient, session, org_a, analyst_a_token,
):
    runner, pdef, vid = await _seed_plugin(session, org_a.id)
    await _make_run(
        session, org_a.id, runner, pdef, vid,
        status="success",
        started_at=datetime.now(UTC) - timedelta(seconds=2),
        ended_at=datetime.now(UTC),
    )
    await session.commit()

    r = await client.get(
        f"/api/v1/plugins/{pdef.id}/stats?window=30d",
        headers=_user_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plugin_id"] == pdef.id
    assert body["window"] == "30d"
    assert body["success"] == 1

    bad = await client.get(
        f"/api/v1/plugins/{pdef.id}/stats?window=bogus",
        headers=_user_h(analyst_a_token, org_a.id),
    )
    assert bad.status_code == 422


async def test_runner_stats_endpoint_requires_superadmin(
    client: AsyncClient, session, org_a, admin_token, analyst_a_token,
):
    runner, pdef, vid = await _seed_plugin(session, org_a.id)
    await session.commit()

    ok = await client.get(
        f"/api/v1/plugin-runners/{runner.id}/stats",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["runner_id"] == runner.id

    denied = await client.get(
        f"/api/v1/plugin-runners/{runner.id}/stats",
        headers=_user_h(analyst_a_token, org_a.id),
    )
    assert denied.status_code in (401, 403)
