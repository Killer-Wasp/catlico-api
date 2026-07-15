"""Plugin runner internal API: registration, heartbeat, sync.

Auth model: a single ``PLUGIN_RUNNER_SHARED_SECRET`` (the autouse ``runner_secret``
fixture, value ``RUNNER_SECRET``) is the whole trust boundary. Runners
self-register and then send every internal call with
``Authorization: Bearer <secret>`` + ``X-Runner-Id``.
"""
from httpx import AsyncClient

# Must match the value the autouse ``runner_secret`` conftest fixture configures.
RUNNER_SECRET = "test-runner-secret"


RUNNER1 = {
    "id": "runner-1",
    "name": "Test Runner",
    "version": "0.1.0",
    "capabilities": ["container", "subprocess"],
    "isolation_mode": "container",
}


def _internal_h(secret, runner_id="runner-1"):
    return {"Authorization": f"Bearer {secret}", "X-Runner-Id": runner_id}


def _org_h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _enable_plugin_for_org(client: AsyncClient, token: str, org_id: str, plugin_id: str):
    h = _org_h(token, org_id)
    r = await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/plugins/{plugin_id}/auto-run/enable", headers=h)
    assert r.status_code == 200, r.text


async def _register_runner(
    client: AsyncClient,
    _unused=None,
    runner: dict | None = None,
    *,
    plugins: list[dict] | None = None,
) -> tuple[dict, str]:
    """Self-register a runner with the shared secret.

    The second positional arg is ignored (legacy call sites pass ``admin_token``);
    registration is now authenticated by the shared secret alone. Returns
    ``(runner_summary, shared_secret)`` — the second element is the shared secret
    callers use as the Bearer for subsequent internal calls (there is no longer a
    per-runner credential to return)."""
    runner = runner or RUNNER1
    body = {**runner}
    if plugins is not None:
        body["plugins"] = plugins
    response = await client.post(
        "/api/internal/plugin-runner/register",
        json=body,
        headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
    )
    assert response.status_code == 200, response.text
    return response.json(), RUNNER_SECRET


async def test_register_requires_shared_secret(client: AsyncClient):
    body = {
        "id": "runner-1",
        "name": "Test Runner",
        "version": "0.1.0",
        "capabilities": ["container"],
        "isolation_mode": "container",
    }
    # No secret at all.
    assert (
        await client.post("/api/internal/plugin-runner/register", json=body)
    ).status_code == 401
    # Wrong secret.
    bad = await client.post(
        "/api/internal/plugin-runner/register",
        json=body,
        headers={"Authorization": "Bearer nope"},
    )
    assert bad.status_code == 401
    # Correct secret self-registers; no secrets are returned.
    ok = await client.post(
        "/api/internal/plugin-runner/register",
        json=body,
        headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["id"] == "runner-1"
    assert data["status"] == "healthy"
    assert "runner_credential" not in data
    assert "push_signing_secret" not in data


async def test_register_creates_and_upserts_runner(client: AsyncClient):
    data, _ = await _register_runner(client, runner=RUNNER1)
    assert data["id"] == "runner-1"
    assert data["name"] == "Test Runner"
    assert data["status"] == "healthy"

    # Upsert: same id, updated name.
    updated = {**RUNNER1, "name": "Runner One"}
    data2, _ = await _register_runner(client, runner=updated)
    assert data2["name"] == "Runner One"

    # The shared secret + X-Runner-Id authenticate internal calls after register.
    heartbeat = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={
            "runner_id": "runner-1",
            "capacity": 1,
            "active_run_count": 0,
            "installed_plugin_count": 0,
            "health_summary": "ok",
        },
        headers=_internal_h(RUNNER_SECRET, "runner-1"),
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "healthy"


async def test_internal_call_rejects_wrong_secret(client: AsyncClient):
    await _register_runner(client, runner=RUNNER1)
    r = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={"runner_id": "runner-1", "capacity": 1, "active_run_count": 0,
              "installed_plugin_count": 0, "health_summary": "ok"},
        headers=_internal_h("wrong-secret", "runner-1"),
    )
    assert r.status_code == 401


async def test_internal_call_unknown_runner_404(client: AsyncClient):
    """A valid secret but an X-Runner-Id with no row is a 404 (identity routing)."""
    r = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={"runner_id": "ghost", "capacity": 1, "active_run_count": 0,
              "installed_plugin_count": 0, "health_summary": "ok"},
        headers=_internal_h(RUNNER_SECRET, "ghost"),
    )
    assert r.status_code == 404


async def test_heartbeat_updates_liveness(client: AsyncClient):
    await _register_runner(client, runner=RUNNER1)
    h = _internal_h(RUNNER_SECRET, "runner-1")

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


async def test_heartbeat_rejects_mismatched_runner(client: AsyncClient):
    """X-Runner-Id (principal) must match the heartbeat body's runner_id."""
    await _register_runner(client, runner=RUNNER1)
    r = await client.post(
        "/api/internal/plugin-runner/heartbeat",
        json={"runner_id": "runner-2", "capacity": 1, "active_run_count": 0,
              "installed_plugin_count": 0, "health_summary": "ok"},
        headers=_internal_h(RUNNER_SECRET, "runner-1"),
    )
    assert r.status_code == 403


async def test_sync_returns_empty_for_new_runner(client: AsyncClient):
    await _register_runner(client, runner=RUNNER1)
    h = _internal_h(RUNNER_SECRET, "runner-1")

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


async def test_register_reports_plugin_manifests(client: AsyncClient):
    """Runner can report installed plugin manifests during registration."""
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")

    # Sync should now include the active plugin.
    sync = await client.get("/api/internal/plugin-runner/sync", headers=h)
    assert sync.status_code == 200
    data = sync.json()
    assert len(data["active_plugins"]) == 1
    assert data["active_plugins"][0]["id"] == "acme-threatintel"


async def test_plugin_version_is_stored_on_register(client: AsyncClient):
    """Each register creates/updates PluginVersion rows."""
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])

    # Re-register with new version.
    v2 = {**SAMPLE_MANIFEST, "version": "1.3.0"}
    await _register_runner(client, runner=RUNNER1, plugins=[v2])
    h = _internal_h(RUNNER_SECRET, "runner-1")

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

    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    await _register_runner(client, runner=runner_two, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")
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
        headers=_internal_h(RUNNER_SECRET, "runner-2"),
    )
    assert second.status_code == 409
    assert second.json()["detail"]["existing_run_id"] == first.json()["run_id"]


# --- Run lifecycle ---


async def test_create_plugin_run(client: AsyncClient, org_a, admin_token):
    """Runner creates a PluginRun row when it decides a plugin should receive an event."""
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")
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
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")

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
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")
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


async def test_run_lifecycle(client: AsyncClient, org_a, admin_token):
    """Full lifecycle: queued → accepted → started → result."""
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")
    await _enable_plugin_for_org(client, admin_token, org_a.id, "acme-threatintel")

    # Create run.
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

    # Accept.
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/accepted", headers=h,
    )
    assert r.status_code == 200

    # Start.
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/started", headers=h,
    )
    assert r.status_code == 200

    # Result.
    r = await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/result",
        json={"status": "success", "result_summary": {"verdict": "info"}, "operation_count": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"


async def test_skip_lifecycle(client: AsyncClient, org_a, admin_token):
    """Runner skips when plugin declines the event."""
    await _register_runner(client, runner=RUNNER1, plugins=[SAMPLE_MANIFEST])
    h = _internal_h(RUNNER_SECRET, "runner-1")
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


async def test_run_unknown_plugin_404(client: AsyncClient, org_a, admin_token):
    await _register_runner(client, runner=RUNNER1)
    h = _internal_h(RUNNER_SECRET, "runner-1")
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
