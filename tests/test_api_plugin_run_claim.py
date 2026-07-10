"""Run-creation claim: freshness skip and global concurrency cap."""
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.models.plugin_runner import PluginResult

from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _enable_plugin_for_org,
    _register_runner,
)
from tests.test_api_plugin_runtime import _create_case_with_observable

_RUNNER_PREFIX = "/api/internal/plugin-runner"


def _runner_h(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def _manifest(**overrides) -> dict:
    return {
        **SAMPLE_MANIFEST,
        "triggers": ["observable.created"],
        "permissions": ["read:observable", "write:observable_enrichment"],
        "configuration": [],
        **overrides,
    }


async def _run_body(org_id, manifest, obs_id, *, event_id):
    return {
        "event_id": event_id,
        "event_type": "observable.created",
        "organisation_id": org_id,
        "plugin_id": manifest["id"],
        "plugin_version": manifest["version"],
        "runner_id": RUNNER1["id"],
        "event_object": {"type": "observable", "id": str(obs_id)},
        "trigger_metadata": {},
    }


async def test_fresh_result_skips_the_run(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(result_ttl_seconds=3600)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    # A non-expired result already exists for this observable.
    session.add(
        PluginResult(
            plugin_run_id=None, organisation_id=org_a.id, plugin_id=manifest["id"],
            entity_type="observable", entity_id=str(obs_id), fingerprint="fp",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await session.commit()

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:fresh"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "skipped"
    assert r.json()["skip_reason"] == "fresh_result"
    assert "runtime_token" not in r.json()


async def test_expired_result_does_not_skip(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(result_ttl_seconds=3600)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    session.add(
        PluginResult(
            plugin_run_id=None, organisation_id=org_a.id, plugin_id=manifest["id"],
            entity_type="observable", entity_id=str(obs_id), fingerprint="fp",
            expires_at=datetime.now(UTC) - timedelta(hours=1),  # stale
        )
    )
    await session.commit()

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:stale"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["runtime_token"]


async def test_tlp_exceeded_skips_the_run(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    # The helper creates the observable at tlp=2; a plugin capped at max_tlp=1 skips.
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(max_tlp=1)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:tlp"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "skipped"
    assert r.json()["skip_reason"] == "tlp_exceeded"


async def test_tlp_within_ceiling_runs(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(max_tlp=2)  # entity tlp=2, ceiling=2 -> ok
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:tlp-ok"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


async def test_concurrency_cap_defers_second_run(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(max_concurrent_runs=1)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    first = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:c1"),
        headers=_runner_h(credential),
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "queued"

    second = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, obs_id, event_id="audit:c2"),
        headers=_runner_h(credential),
    )
    assert second.status_code == 429, second.text
