"""Public API: plugin catalog and config (org-scoped)."""
from httpx import AsyncClient

from tests.test_api_plugin_runners import SAMPLE_MANIFEST, _enable_plugin_for_org, _register_runner
from tests.test_api_plugin_runtime import _runtime_token_for


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _internal_h(secret):
    return {"Authorization": f"Bearer {secret}"}


def _runtime_h(token):
    return {"Authorization": f"Bearer {token}"}


# --- Fixture helpers ---


async def _setup_runner_and_plugin(
    client: AsyncClient,
    admin_token: str,
    runner_id: str = "runner-1",
) -> tuple[str, str]:
    """Register a runner with a sample plugin, returns (plugin_id, credential)."""
    from tests.test_api_plugin_runners import RUNNER1

    _, credential = await _register_runner(
        client,
        admin_token,
        {**RUNNER1, "id": runner_id},
        plugins=[SAMPLE_MANIFEST],
    )
    return SAMPLE_MANIFEST["id"], credential


async def _setup_enabled_plugin(
    client: AsyncClient,
    admin_token: str,
    org_id: str,
    runner_id: str = "runner-1",
) -> tuple[str, str]:
    plugin_id, credential = await _setup_runner_and_plugin(
        client,
        admin_token,
        runner_id,
    )
    await _enable_plugin_for_org(client, admin_token, org_id, plugin_id)
    return plugin_id, credential


# --- Plugin catalog ---


async def test_list_plugins_empty(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get("/api/v1/plugins", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200
    assert r.json() == []


async def test_list_plugins_with_registered(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.get("/api/v1/plugins", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200
    plugins = r.json()
    assert len(plugins) == 1
    assert plugins[0]["id"] == plugin_id
    assert plugins[0]["display_name"] == "Acme Threat Intel"


async def test_get_plugin_by_id(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.get(
        f"/api/v1/plugins/{plugin_id}", headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200
    assert r.json()["id"] == plugin_id
    assert "manifest" in r.json()


async def test_get_unknown_plugin_404(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get(
        "/api/v1/plugins/ghost", headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 404


async def test_plugin_resource_get_is_proxied_through_api(
    client: AsyncClient, runner_secret, admin_token, org_a, monkeypatch,
):
    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    async def fake_resource(base_url: str, method: str, path: str, body=None):
        assert base_url == "http://runner:8080"
        assert method == "GET"
        assert path == f"/internal/plugins/{plugin_id}/resources/options"
        assert body is None
        return {"options": ["one", "two"]}

    monkeypatch.setattr(
        "app.api.v1.routes.plugins._runner_resource_json",
        fake_resource,
    )

    r = await client.get(
        f"/api/v1/plugins/{plugin_id}/resources/options",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"options": ["one", "two"]}


async def test_plugin_resource_post_requires_write_permission(
    client: AsyncClient, runner_secret, readonly_a_token, org_a, admin_token,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/resources/options",
        json={"q": "x"},
        headers=_h(readonly_a_token, org_a.id),
    )
    assert r.status_code == 403


# --- Plugin enable / disable ---


async def test_enable_plugin(
    client: AsyncClient, runner_secret, org_a, analyst_a_token, admin_token,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(analyst_a_token, org_a.id)

    r = await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True


async def test_disable_plugin(
    client: AsyncClient, runner_secret, org_a, analyst_a_token, admin_token,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(analyst_a_token, org_a.id)

    await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)
    r = await client.post(f"/api/v1/plugins/{plugin_id}/disable", headers=h)
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_readonly_user_cannot_enable_plugin(
    client: AsyncClient, runner_secret, org_a, readonly_a_token, admin_token,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/enable",
        headers=_h(readonly_a_token, org_a.id),
    )
    assert r.status_code == 403


# --- Plugin config ---


async def test_superadmin_can_set_plugin_config(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)

    r = await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={
            "settings": {"timeout": 60},
            "secrets": {"api_key": "s3cr3t"},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["settings"] == {"timeout": 60}
    assert data["has_secrets"] is True
    assert "s3cr3t" not in r.text


async def test_get_plugin_config_returns_stored_settings(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)

    # Empty before anything is stored.
    r = await client.get(f"/api/v1/plugins/{plugin_id}/config", headers=h)
    assert r.status_code == 200
    assert r.json() == {"settings": {}, "has_secrets": False}

    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {"timeout": 42}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )
    r = await client.get(f"/api/v1/plugins/{plugin_id}/config", headers=h)
    data = r.json()
    assert data["settings"] == {"timeout": 42}
    assert data["has_secrets"] is True
    assert "s3cr3t" not in r.text  # secrets never returned in the clear


async def test_config_status_and_completeness_track_required_secret(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    # SAMPLE_MANIFEST requires the `api_key` secret.
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)

    r = await client.get(f"/api/v1/plugins/{plugin_id}/config/status", headers=h)
    assert r.status_code == 200
    status_map = r.json()
    assert status_map["api_key"]["status"] == "missing"
    assert status_map["api_key"]["secret_configured"] is False

    # Catalog reflects incompleteness.
    plugins = (await client.get("/api/v1/plugins", headers=h)).json()
    assert plugins[0]["config_complete"] is False

    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )

    status_map = (await client.get(f"/api/v1/plugins/{plugin_id}/config/status", headers=h)).json()
    assert status_map["api_key"]["status"] == "configured"
    assert status_map["api_key"]["secret_configured"] is True
    plugins = (await client.get("/api/v1/plugins", headers=h)).json()
    assert plugins[0]["config_complete"] is True


async def test_config_secret_merge_keeps_and_deletes(
    client: AsyncClient, session, runner_secret, admin_token, org_a,
):
    """Absent key keeps, string replaces, null deletes — a partial secret update
    must not drop other stored secrets."""
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)

    from app.core.crypto import decrypt_secrets
    from app.models.plugin_runner import PluginConfig
    from sqlalchemy import select

    async def stored_secret_keys():
        # Read the persisted config on the shared test session (the client commits
        # after each request); expire first to defeat the identity-map cache.
        session.expire_all()
        cfg = (
            await session.execute(
                select(PluginConfig).where(PluginConfig.plugin_id == plugin_id)
            )
        ).scalars().first()
        return set(decrypt_secrets(cfg.secrets_encrypted).keys())

    # Store two secrets.
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "a", "extra": "b"}},
        headers=h,
    )
    assert await stored_secret_keys() == {"api_key", "extra"}

    # Update only api_key; `extra` must survive.
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "a2"}},
        headers=h,
    )
    assert await stored_secret_keys() == {"api_key", "extra"}

    # Null deletes just that key.
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"extra": None}},
        headers=h,
    )
    assert await stored_secret_keys() == {"api_key"}


async def test_superadmin_can_test_plugin_config(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)

    # Without config, test should fail
    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/config/test",
        headers=h,
    )
    assert r.status_code == 409  # missing required secret

    # With config, test passes
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )
    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/config/test",
        headers=h,
    )
    assert r.status_code == 200, r.text


# --- Plugin runs (public view) ---


async def test_list_plugin_runs(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    # Create a run via internal API
    await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:1",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )

    # Public list
    r = await client.get(
        "/api/v1/plugin-runs", headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "queued"


async def test_get_plugin_run_detail(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:2",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]

    r = await client.get(
        f"/api/v1/plugin-runs/{run_id}", headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200
    assert r.json()["id"] == run_id


async def test_manual_plugin_run_for_observable(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    await client.post(
        f"/api/v1/plugins/{plugin_id}/enable",
        headers=_h(admin_token, org_a.id),
    )

    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/",
        json={"title": "Manual run", "description": ""},
        headers=h,
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4", "tlp": 2, "pap": 2},
        headers=h,
    )

    r = await client.post(
        f"/api/v1/observables/{observable.json()['id']}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["plugin_id"] == plugin_id
    assert data["event_type"] == "observable.manual"
    assert data["event_object_type"] == "observable"
    assert data["event_object_id"] == observable.json()["id"]
    assert data["status"] == "queued"

    again = await client.post(
        f"/api/v1/observables/{observable.json()['id']}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    assert again.status_code == 200, again.text
    # Deterministic manual event_id: a duplicate submission resolves to the same
    # run identity, so the plugin cannot be double-run for the same entity.
    assert again.json()["id"] == data["id"]
    assert again.json()["event_id"] == data["event_id"]


async def test_manual_plugin_run_rejects_disabled_plugin(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/",
        json={"title": "Manual run", "description": ""},
        headers=h,
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4", "tlp": 2, "pap": 2},
        headers=h,
    )

    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/run",
        json={"entity_type": "observable", "entity_id": observable.json()["id"]},
        headers=h,
    )
    assert r.status_code == 409


# --- Auto-run ---


async def test_auto_run_enable_disable(
    client: AsyncClient, runner_secret, org_a, analyst_a_token, admin_token,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(analyst_a_token, org_a.id)

    # Enable plugin first
    await client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=h)

    r = await client.post(f"/api/v1/plugins/{plugin_id}/auto-run/enable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["auto_run_enabled"] is True

    r = await client.post(f"/api/v1/plugins/{plugin_id}/auto-run/disable", headers=h)
    assert r.status_code == 200
    assert r.json()["auto_run_enabled"] is False


# --- Cancel / Retry ---


async def test_cancel_plugin_run(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:3",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]

    r = await client.post(
        f"/api/v1/plugin-runs/{run_id}/cancel",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


async def test_retry_failed_runs(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    # Create and fail a run
    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:4",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]
    await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/result",
        json={"status": "failure", "error": "something broke"},
        headers=h,
    )

    r = await client.post(
        "/api/v1/plugin-runs/retry-failed",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["retried"] >= 0


async def test_clear_finished_runs(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)
    oh = _h(admin_token, org_a.id)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "clear:1",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]
    await client.post(
        f"/api/internal/plugin-runner/runs/{run_id}/result",
        json={"status": "success"},
        headers=h,
    )
    assert len((await client.get("/api/v1/plugin-runs", headers=oh)).json()) == 1

    r = await client.post("/api/v1/plugin-runs/clear-finished", headers=oh)
    assert r.status_code == 200, r.text
    assert r.json()["cleared"] == 1
    assert (await client.get("/api/v1/plugin-runs", headers=oh)).json() == []


# --- Run-scoped config ---


async def test_run_config_fetch(
    client: AsyncClient, runner_secret: str, admin_token, org_a,
):
    """Runner fetches decrypted config for a specific run."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    # Set plugin config
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {"timeout": 30}, "secrets": {"api_key": "s3cr3t"}},
        headers=_h(admin_token, org_a.id),
    )

    # Create a run
    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:5",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]

    # Accept the run (config only available for accepted/running runs)
    await client.post(f"/api/internal/plugin-runner/runs/{run_id}/accepted", headers=h)

    # Fetch config
    r = await client.get(
        f"/api/internal/plugin-runner/runs/{run_id}/config", headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["settings"] == {"timeout": 30}
    # Secrets are included (decrypted) for the run
    assert "secrets" in data


async def test_run_config_rejected_for_queued_run(
    client: AsyncClient, runner_secret: str, org_a, admin_token,
):
    """Config is not available for queued (unaccepted) runs."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:6",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]

    r = await client.get(
        f"/api/internal/plugin-runner/runs/{run_id}/config", headers=h,
    )
    assert r.status_code == 409


# --- Security: cross-org and permission tests ---


async def test_cancel_run_cross_org_rejected(
    client: AsyncClient, runner_secret, org_a, org_b, analyst_a_token, analyst_b_token, admin_token,
):
    """A run in org-a cannot be cancelled by org-b."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:cross",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    run_id = r.json()["run_id"]

    # org-b tries to cancel org-a's run
    r = await client.post(
        f"/api/v1/plugin-runs/{run_id}/cancel",
        headers=_h(analyst_b_token, org_b.id),
    )
    assert r.status_code == 404


async def test_list_runs_scoped_to_org(
    client: AsyncClient, runner_secret, admin_token, org_a, org_b,
):
    """Plugin runs list is scoped to the caller's org."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)

    await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "audit:a1",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )

    r = await client.get("/api/v1/plugin-runs", headers=_h(admin_token, org_b.id))
    assert r.status_code == 200
    assert r.json() == []


# --- Missing runtime APIs (Finding #9) ---


async def test_add_task_log(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    """Plugin can append a task log entry."""
    # Create case + task via public API
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "Test", "description": "Desc"},
        headers=h,
    )
    case_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/cases/{case_id}/tasks",
        json={"title": "Investigate"},
        headers=h,
    )
    task_id = r.json()["id"]
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    # Append task log via runtime
    r = await client.post(
        f"/api/internal/plugin-runtime/cases/{case_id}/tasks/{task_id}/logs",
        json={"message": "Plugin found malicious indicators"},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text


async def test_add_tag(
    client: AsyncClient, org_a, analyst_a_token, admin_token, runner_secret,
):
    """Plugin proposes a tag on a case (canonical edits are not applied directly)."""
    # Create case + tag via public API
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "Test", "description": "Desc"},
        headers=h,
    )
    case_id = r.json()["id"]
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    # Create a tag first (needed for validation)
    await client.post(
        "/api/v1/tags",
        json={"name": "phishing", "colour": "#ff0000"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )

    # Propose tag via runtime — returns a proposed action, not a direct edit.
    r = await client.post(
        f"/api/internal/plugin-runtime/cases/{case_id}/tags",
        json={"tag": "phishing"},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "proposed"


async def test_patch_observable(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    """Plugin can patch observable metadata."""
    # Create case + observable
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "Test", "description": "Desc"},
        headers=h,
    )
    case_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": "5.6.7.8", "tlp": 2, "pap": 2},
        headers=h,
    )
    obs_id = r.json()["id"]
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=obs_id
    )

    r = await client.patch(
        f"/api/internal/plugin-runtime/observables/{obs_id}",
        json={"ioc": True, "message": "Flagged by plugin"},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text
