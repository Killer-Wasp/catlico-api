"""Run-creation claim: freshness skip and global concurrency cap."""
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlmodel import select

from app.models.plugin_runner import PluginEventDelivery, PluginResult, PluginRun
from app.services.plugin_dispatch import build_manual_envelope, manual_event_id

from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _enable_plugin_for_org,
    _register_runner,
)
from tests.test_api_plugin_runtime import _create_case_with_observable

_RUNNER_PREFIX = "/api/internal/plugin-runner"


def _runner_h(secret: str, runner_id: str = "runner-1") -> dict:
    return {"Authorization": f"Bearer {secret}", "X-Runner-Id": runner_id}


def _admin_org_h(admin_token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_id}


async def _enable_only(client, admin_token, org_id, plugin_id):
    """Enable a plugin for an org WITHOUT auto-run (manual-run scenario)."""
    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/enable",
        headers=_admin_org_h(admin_token, org_id),
    )
    assert r.status_code == 200, r.text


async def _store_manual_delivery(session, org_id, plugin_id, obs_id, runner_id):
    """Persist the API-authored manual envelope create_run reads back."""
    envelope = build_manual_envelope(
        org_id=org_id, plugin_id=plugin_id, entity_type="observable",
        entity_id=str(obs_id), actor="user:analyst",
    )
    session.add(
        PluginEventDelivery(
            event_id=envelope["event_id"], runner_id=runner_id,
            envelope=envelope, status="delivered",
        )
    )
    await session.commit()
    return envelope


def _manual_claim_body(org_id, manifest, obs_id, event_id, **extra):
    return {
        "event_id": event_id,
        "event_type": "observable.manual",
        "organisation_id": org_id,
        "plugin_id": manifest["id"],
        "plugin_version": manifest["version"],
        "runner_id": RUNNER1["id"],
        "event_object": {"type": "observable", "id": str(obs_id)},
        "trigger_metadata": {},
        **extra,
    }


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


# --- Manual (targeted) runs: analyst intent wins, safety guards stay ---


async def test_manual_run_bypasses_trigger_autorun_and_freshness(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    """A manual run executes even though (a) observable.manual is not a declared
    trigger, (b) auto-run is OFF, and (c) a fresh cached result exists."""
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(result_ttl_seconds=3600)  # triggers=[observable.created]
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_only(client, admin_token, org_a.id, manifest["id"])  # NO auto-run

    session.add(
        PluginResult(
            plugin_run_id=None, organisation_id=org_a.id, plugin_id=manifest["id"],
            entity_type="observable", entity_id=str(obs_id), fingerprint="fp",
            expires_at=datetime.now(UTC) + timedelta(hours=1),  # fresh
        )
    )
    envelope = await _store_manual_delivery(
        session, org_a.id, manifest["id"], obs_id, RUNNER1["id"]
    )

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=_manual_claim_body(org_a.id, manifest, obs_id, envelope["event_id"]),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["runtime_token"]


async def test_runner_cannot_forge_manual_in_the_claim_body(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    """A runner asserting manual for an event the API never marked manual gets
    the strict path — the body flag is not trusted."""
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest()  # triggers=[observable.created]
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_only(client, admin_token, org_a.id, manifest["id"])  # NO auto-run

    # No manual delivery stored. Runner lies in the body.
    forged = manual_event_id(org_a.id, manifest["id"], "observable", str(obs_id))
    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=_manual_claim_body(
            org_a.id, manifest, obs_id, forged,
            manual=True, target_plugin_id=manifest["id"],
        ),
        headers=_runner_h(credential),
    )
    # observable.manual is not a declared trigger -> strict trigger guard 409s.
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Plugin does not declare this event trigger"


async def test_manual_run_still_enforces_tlp_ceiling(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)  # tlp=2
    manifest = _manifest(max_tlp=1)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_only(client, admin_token, org_a.id, manifest["id"])
    envelope = await _store_manual_delivery(
        session, org_a.id, manifest["id"], obs_id, RUNNER1["id"]
    )

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=_manual_claim_body(org_a.id, manifest, obs_id, envelope["event_id"]),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "skipped"
    assert r.json()["skip_reason"] == "tlp_exceeded"


async def test_manual_run_still_enforces_concurrency_cap(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = _manifest(max_tlp=2, max_concurrent_runs=1)
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_only(client, admin_token, org_a.id, manifest["id"])

    # One live run already occupies the single concurrency slot.
    session.add(
        PluginRun(
            event_id="audit:live", event_type="observable.created",
            organisation_id=org_a.id, plugin_id=manifest["id"], runner_id=RUNNER1["id"],
            status="running",
        )
    )
    envelope = await _store_manual_delivery(
        session, org_a.id, manifest["id"], obs_id, RUNNER1["id"]
    )

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=_manual_claim_body(org_a.id, manifest, obs_id, envelope["event_id"]),
        headers=_runner_h(credential),
    )
    assert r.status_code == 429, r.text


# --- Retry reuse vs. multi-runner arbitration ---


async def test_retry_reuses_requeued_run_and_remints_token(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    """A queued run owned by the claiming runner (post retry-failed) is reused —
    same row, re-minted token — not duplicated and not rejected."""
    manifest = _manifest()
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    run = PluginRun(
        event_id="audit:retry", event_type="observable.created",
        organisation_id=org_a.id, plugin_id=manifest["id"], runner_id=RUNNER1["id"],
        status="queued", attempt=2,
    )
    session.add(run)
    await session.commit()
    run_id = str(run.id)

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, "", event_id="audit:retry"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == run_id  # reused, not a new row
    assert r.json()["status"] == "queued"
    assert r.json()["runtime_token"]

    rows = (
        await session.execute(
            select(PluginRun).where(
                PluginRun.event_id == "audit:retry",
                PluginRun.plugin_id == manifest["id"],
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_concurrent_duplicate_of_live_run_still_409s(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    """The (event_id, plugin_id) arbiter is not weakened: an active run cannot be
    re-claimed, even by its owning runner."""
    manifest = _manifest()
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    run = PluginRun(
        event_id="audit:inflight", event_type="observable.created",
        organisation_id=org_a.id, plugin_id=manifest["id"], runner_id=RUNNER1["id"],
        status="running",  # active, not queued
    )
    session.add(run)
    await session.commit()

    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, "", event_id="audit:inflight"),
        headers=_runner_h(credential),
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["existing_run_id"] == str(run.id)


# --- retry-failed endpoint: reset + genuine re-dispatch ---


async def test_retry_failed_requeues_and_resets_delivery(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    manifest = _manifest()
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    created = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, "", event_id="audit:rf"),
        headers=_runner_h(credential),
    )
    assert created.status_code == 200, created.text
    run_id = uuid.UUID(created.json()["run_id"])

    run = await session.get(PluginRun, run_id)
    run.status = "failure"
    run.attempt = 1
    session.add(
        PluginEventDelivery(
            event_id="audit:rf", runner_id=RUNNER1["id"],
            envelope={"event_id": "audit:rf"}, status="delivered",
            delivered_at=datetime.now(UTC),
        )
    )
    await session.commit()

    r = await client.post(
        "/api/v1/plugin-runs/retry-failed",
        headers=_admin_org_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert r.json()["redispatched"] == 1

    session.expire_all()
    run = await session.get(PluginRun, run_id)
    assert run.status == "queued"
    assert run.attempt == 2
    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(PluginEventDelivery.event_id == "audit:rf")
        )
    ).scalar_one()
    assert delivery.status == "pending"
    assert delivery.delivered_at is None


async def test_retry_failed_handles_missing_delivery(
    client: AsyncClient, session, org_a, analyst_a_token, runner_secret, admin_token,
):
    """No surviving delivery row -> a fresh pending delivery is synthesized."""
    manifest = _manifest()
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    await _enable_plugin_for_org(client, admin_token, org_a.id, manifest["id"])

    created = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json=await _run_body(org_a.id, manifest, "", event_id="audit:rf2"),
        headers=_runner_h(credential),
    )
    run_id = uuid.UUID(created.json()["run_id"])
    run = await session.get(PluginRun, run_id)
    run.status = "failure"
    await session.commit()  # note: no delivery row exists

    r = await client.post(
        "/api/v1/plugin-runs/retry-failed",
        headers=_admin_org_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert r.json()["redispatched"] == 1

    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(PluginEventDelivery.event_id == "audit:rf2")
        )
    ).scalar_one()
    assert delivery.status == "pending"
    assert delivery.runner_id == RUNNER1["id"]
