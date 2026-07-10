"""Plugin runner internal API: registration, heartbeat, sync."""
from httpx import AsyncClient


RUNNER1 = {
    "id": "runner-1",
    "name": "Test Runner",
    "version": "0.1.0",
    "capabilities": ["container", "subprocess"],
    "isolation_mode": "container",
}


def _internal_h(secret):
    return {"Authorization": f"Bearer {secret}"}


def _org_h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _enable_plugin_for_org(client: AsyncClient, token: str, org_id: str, plugin_id: str):
    h = _org_h(token, org_id)
    r = await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/plugins/{plugin_id}/auto-run/enable", headers=h)
    assert r.status_code == 200, r.text


async def _create_enrollment_token(
    client: AsyncClient,
    admin_token: str,
    runner_id: str = "runner-1",
    *,
    name: str = "Test Runner",
) -> str:
    response = await client.post(
        "/api/v1/plugin-runners",
        json={"id": runner_id, "name": name, "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["enrollment_token"]
    return data["enrollment_token"]


async def _register_runner(
    client: AsyncClient,
    admin_token: str,
    runner: dict | None = None,
    *,
    plugins: list[dict] | None = None,
) -> tuple[dict, str]:
    runner = runner or RUNNER1
    token = await _create_enrollment_token(
        client,
        admin_token,
        runner["id"],
        name=runner.get("name", ""),
    )
    body = {**runner, "enrollment_token": token}
    if plugins is not None:
        body["plugins"] = plugins
    response = await client.post("/api/internal/plugin-runner/register", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["runner_credential"]
    assert data["push_signing_secret"]
    return data, data["runner_credential"]


async def test_register_requires_valid_enrollment_token(
    client: AsyncClient, admin_token: str,
):
    body = {
        "id": "runner-1",
        "name": "Test Runner",
        "version": "0.1.0",
        "capabilities": ["container"],
        "isolation_mode": "container",
    }
    assert (
        await client.post("/api/internal/plugin-runner/register", json=body)
    ).status_code == 401
    bad = await client.post(
        "/api/internal/plugin-runner/register",
        json={**body, "enrollment_token": "nope"},
    )
    assert bad.status_code == 401
    token = await _create_enrollment_token(client, admin_token)
    ok = await client.post(
        "/api/internal/plugin-runner/register",
        json={**body, "enrollment_token": token},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["runner_credential"].startswith("cpr_")
    assert data["push_signing_secret"].startswith("cps_")

    reused = await client.post(
        "/api/internal/plugin-runner/register",
        json={**body, "enrollment_token": token},
    )
    assert reused.status_code == 401


async def test_register_creates_and_upserts_runner(
    client: AsyncClient, admin_token: str,
):
    data, credential = await _register_runner(client, admin_token, RUNNER1)
    assert data["id"] == "runner-1"
    assert data["name"] == "Test Runner"
    assert data["status"] == "healthy"

    # Upsert: same id, updated name
    token = await _create_enrollment_token(
        client,
        admin_token,
        "runner-1",
        name="Runner One",
    )
    updated = {**RUNNER1, "name": "Runner One"}
    r2 = await client.post(
        "/api/internal/plugin-runner/register",
        json={**updated, "enrollment_token": token},
    )
    assert r2.status_code == 200
    assert r2.json()["name"] == "Runner One"

    heartbeat = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={
            "runner_id": "runner-1",
            "capacity": 1,
            "active_run_count": 0,
            "installed_plugin_count": 0,
            "health_summary": "ok",
        },
        headers=_internal_h(credential),
    )
    assert heartbeat.status_code == 401


async def test_heartbeat_updates_liveness(
    client: AsyncClient, admin_token: str,
):
    _, credential = await _register_runner(client, admin_token, RUNNER1)
    h = _internal_h(credential)

    # Heartbeat
    heartbeat = {
        "runner_id": "runner-1",
        "capacity": 10,
        "active_run_count": 2,
        "installed_plugin_count": 5,
        "health_summary": "ok",
    }
    r = await client.post(
        "/api/internal/plugin-runner/heartbeat", json=heartbeat, headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "healthy"


async def test_heartbeat_rejects_unknown_runner(
    client: AsyncClient, admin_token: str,
):
    _, credential = await _register_runner(client, admin_token, RUNNER1)
    h = _internal_h(credential)
    r = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={"runner_id": "ghost", "capacity": 1, "active_run_count": 0,
              "installed_plugin_count": 0, "health_summary": "ok"},
        headers=h,
    )
    assert r.status_code == 403


async def test_sync_returns_empty_for_new_runner(
    client: AsyncClient, admin_token: str,
):
    _, credential = await _register_runner(client, admin_token, RUNNER1)
    h = _internal_h(credential)

    r = await client.get("/api/internal/plugin-runner/sync", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "active_plugins" in data
    assert "org_enablements" in data


# --- Plugin definition registration (runner reports manifests) ---

SAMPLE_MANIFEST = {
    "id": "acme-threatintel",
    "name": "Acme Threat Intel",
    "version": "1.2.0",
    "sdk": ">=0.1,<1",
    "runtime": "python",
    "entrypoint": "acme_threatintel.plugin:Plugin",
    "capabilities": ["enrichment"],
    "triggers": ["observable.created"],
    "permissions": ["read:observable", "write:observable_enrichment"],
    "timeout_seconds": 60,
    "configuration": [{"name": "api_key", "type": "string", "secret": True, "required": True}],
}


async def test_register_reports_plugin_manifests(
    client: AsyncClient, admin_token: str,
):
    """Runner can report installed plugin manifests during registration."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)

    # Sync should now include the active plugin
    sync = await client.get("/api/internal/plugin-runner/sync", headers=h)
    assert sync.status_code == 200
    data = sync.json()
    assert len(data["active_plugins"]) == 1
    assert data["active_plugins"][0]["id"] == "acme-threatintel"


async def test_plugin_version_is_stored_on_register(
    client: AsyncClient, admin_token: str,
):
    """Each register creates/updates PluginVersion rows."""
    await _register_runner(client, admin_token, RUNNER1, plugins=[SAMPLE_MANIFEST])

    # Re-register with new version
    v2 = {**SAMPLE_MANIFEST, "version": "1.3.0"}
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[v2])
    h = _internal_h(credential)

    sync = await client.get("/api/internal/plugin-runner/sync", headers=h)
    assert sync.status_code == 200
    plugins = sync.json()["active_plugins"]
    assert len(plugins) == 1
    assert plugins[0]["version"] == "1.3.0"


async def test_two_runners_hosting_same_plugin_claim_one_run(
    client: AsyncClient, org_a, admin_token,
):
    """A plugin installed on two runners still creates one run per event/plugin."""
    runner_two = {**RUNNER1, "id": "runner-2", "name": "Runner Two"}

    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    _, runner_two_credential = await _register_runner(
        client,
        admin_token,
        runner_two,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    run_body = {
        "event_id": "audit:shared-plugin",
        "event_type": "observable.created",
        "organisation_id": org_a.id,
        "plugin_id": "acme-threatintel",
        "plugin_version": "1.2.0",
        "runner_id": "runner-1",
        "trigger_metadata": {},
    }
    first = await client.post(
        "/api/internal/plugin-runner/runs", json=run_body, headers=h,
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        "/api/internal/plugin-runner/runs",
        json={**run_body, "runner_id": "runner-2"},
        headers=_internal_h(runner_two_credential),
    )
    assert second.status_code == 409
    assert second.json()["detail"]["existing_run_id"] == first.json()["run_id"]


# --- Run lifecycle ---


async def test_create_plugin_run(
    client: AsyncClient, org_a, admin_token,
):
    """Runner creates a PluginRun row when it decides a plugin should receive an event."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    run_body = {
        "event_id": "audit:abc123",
        "event_type": "observable.created",
        "organisation_id": org_a.id,
        "plugin_id": "acme-threatintel",
        "plugin_version": "1.2.0",
        "runner_id": "runner-1",
        "trigger_metadata": {"observable_type": "ip", "data": "1.2.3.4"},
    }
    r = await client.post(
        "/api/internal/plugin-runner/runs", json=run_body, headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "run_id" in data
    assert "runtime_token" in data
    assert data["status"] == "queued"


async def test_create_plugin_run_requires_org_auto_run(
    client: AsyncClient, org_a, admin_token,
):
    """Runner cannot create event runs for plugins not enabled for org auto-run."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:not-enabled",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": "acme-threatintel",
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    assert r.status_code == 409


async def test_create_plugin_run_rejects_undeclared_trigger(
    client: AsyncClient, org_a, admin_token,
):
    """Runner cannot create a run for an event type absent from the manifest."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:wrong-trigger",
            "event_type": "case.created",
            "organisation_id": org_a.id,
            "plugin_id": "acme-threatintel",
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    assert r.status_code == 409


async def test_run_lifecycle(
    client: AsyncClient, org_a, admin_token,
):
    """Full lifecycle: queued → accepted → started → result."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    # Create run
    run_body = {
        "event_id": "audit:abc123",
        "event_type": "observable.created",
        "organisation_id": org_a.id,
        "plugin_id": "acme-threatintel",
        "plugin_version": "1.2.0",
        "runner_id": "runner-1",
        "trigger_metadata": {},
    }
    r = await client.post("/api/internal/plugin-runner/runs", json=run_body, headers=h)
    run_id = r.json()["run_id"]

    # Accept
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/accepted", headers=h,
    )
    assert r.status_code == 200

    # Start
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/started", headers=h,
    )
    assert r.status_code == 200

    # Result
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/result",
        json={"status": "success", "result_summary": {"verdict": "info"}, "operation_count": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"


async def test_skip_lifecycle(
    client: AsyncClient, org_a, admin_token,
):
    """Runner skips when plugin declines the event."""
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[SAMPLE_MANIFEST],
    )
    h = _internal_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    run_body = {
        "event_id": "audit:xyz",
        "event_type": "observable.created",
        "organisation_id": org_a.id,
        "plugin_id": "acme-threatintel",
        "plugin_version": "1.2.0",
        "runner_id": "runner-1",
        "trigger_metadata": {},
    }
    r = await client.post("/api/internal/plugin-runner/runs", json=run_body, headers=h)
    run_id = r.json()["run_id"]

    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/skipped",
        json={"skip_reason": "Not applicable for domain observables"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"


async def test_run_unknown_plugin_404(
    client: AsyncClient, org_a, admin_token,
):
    _, credential = await _register_runner(client, admin_token, RUNNER1)
    h = _internal_h(credential)
    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:x",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": "nonexistent",
            "plugin_version": "1.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    assert r.status_code == 404
