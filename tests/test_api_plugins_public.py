"""Public API: plugin catalog and config (org-scoped)."""
from httpx import AsyncClient
from sqlalchemy import select

from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _enable_plugin_for_org,
    _register_runner,
)
from tests.test_api_plugin_runtime import _runtime_token_for


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _internal_h(secret, runner_id="runner-1"):
    return {"Authorization": f"Bearer {secret}", "X-Runner-Id": runner_id}


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
        {**RUNNER1, "id": runner_id, "base_url": "http://runner:8080"},
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
    body = r.json()
    assert body["enabled"] is True
    # Returns the full plugin view, not a bare `{enabled}` — the client renders
    # display_name from it (regression guard for "undefined enabled" toasts).
    assert body["id"] == plugin_id
    assert body["display_name"]


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


async def test_config_test_clears_auto_suspension(
    client: AsyncClient, runner_secret, admin_token, org_a, session,
):
    """A passing config test lifts a circuit-breaker auto-suspension."""
    from app.models.plugin_runner import OrgPlugin

    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    h = _h(admin_token, org_a.id)
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )

    # Simulate a tripped breaker on the org's plugin.
    op = await session.get(OrgPlugin, (org_a.id, plugin_id))
    if op is None:
        op = OrgPlugin(organisation_id=org_a.id, plugin_id=plugin_id, enabled=True)
        session.add(op)
    op.suspended_reason = "Auto-suspended after 3 consecutive configuration failures."
    op.config_failure_streak = 3
    await session.flush()

    r = await client.post(f"/api/v1/plugins/{plugin_id}/config/test", headers=h)
    assert r.status_code == 200, r.text

    refreshed = await session.get(OrgPlugin, (org_a.id, plugin_id))
    assert refreshed.suspended_reason is None
    assert refreshed.config_failure_streak == 0


# A required secret declared via `type = "secret"` alone — no `secret: true`
# boolean. `_is_secret_param` accepts this convention; the config/test route must
# agree, otherwise it reports the plugin "valid" while the enable gate blocks it.
_SECRET_TYPE_MANIFEST = {
    **SAMPLE_MANIFEST,
    "id": "secret-type-plugin",
    "configuration": [{"name": "api_key", "type": "secret", "required": True}],
}


async def test_config_test_recognises_type_secret_convention(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """config/test must treat a required secret declared as `type = "secret"`
    (without `secret: true`) as missing when unset and satisfied once stored —
    consistent with `_is_secret_param`, the single source of truth the config
    status / enable gate uses."""
    await _register_runner(
        client,
        admin_token,
        {**RUNNER1, "id": "runner-secret-type"},
        plugins=[_SECRET_TYPE_MANIFEST],
    )
    plugin_id = _SECRET_TYPE_MANIFEST["id"]
    h = _h(admin_token, org_a.id)

    # Unset: config/test must flag the required secret as missing (409), not pass.
    r = await client.post(f"/api/v1/plugins/{plugin_id}/config/test", headers=h)
    assert r.status_code == 409, r.text
    assert "api_key" in r.json()["detail"]["missing"]

    # Once stored, config/test passes.
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )
    r = await client.post(f"/api/v1/plugins/{plugin_id}/config/test", headers=h)
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


async def _run_on_observable(
    client: AsyncClient,
    credential: str,
    *,
    org_id: str,
    plugin_id: str,
    observable_id: str,
    event_id: str,
) -> str:
    """Queue a run delivered an observable, via the runner's internal API."""
    r = await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": event_id,
            "event_type": "observable.created",
            "organisation_id": org_id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "event_object_type": "observable",
            "event_object_id": observable_id,
            "trigger_metadata": {},
        },
        headers=_internal_h(credential),
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["run_id"]


async def test_plugin_run_views_resolve_observable_value(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    """The runs queue shows *what* each run analysed, but the run row holds only the
    object's uuid — so the list and detail views resolve its type + value."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)

    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "Runs", "description": ""}, headers=h
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "8.8.8.8"},
        headers=h,
    )
    run_id = await _run_on_observable(
        client,
        credential,
        org_id=org_a.id,
        plugin_id=plugin_id,
        observable_id=observable.json()["id"],
        event_id="audit:obs-value",
    )

    listed = await client.get("/api/v1/plugin-runs", headers=_h(admin_token, org_a.id))
    assert listed.status_code == 200, listed.text
    assert [(r["observable_type"], r["observable_value"]) for r in listed.json()] == [
        ("ip", "8.8.8.8")
    ]

    detail = await client.get(
        f"/api/v1/plugin-runs/{run_id}", headers=_h(admin_token, org_a.id)
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["observable_value"] == "8.8.8.8"


async def test_plugin_run_hides_value_of_invisible_observable(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a, org_b,
):
    """The runs list is gated on read:connector and the run's own org, but an
    observable rides case visibility — so resolving the value must re-check it,
    or watching the queue would leak values off cases the org cannot open."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    await _enable_plugin_for_org(client, admin_token, org_b.id, plugin_id)

    # Observable on a case owned by org_a, never shared with org_b.
    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "Private", "description": ""}, headers=h
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "10.9.9.9"},
        headers=h,
    )

    # ...delivered to a run owned by org_b (the shape left behind when a share is
    # revoked after the run).
    await _run_on_observable(
        client,
        credential,
        org_id=org_b.id,
        plugin_id=plugin_id,
        observable_id=observable.json()["id"],
        event_id="audit:obs-hidden",
    )

    listed = await client.get("/api/v1/plugin-runs", headers=_h(admin_token, org_b.id))
    assert listed.status_code == 200, listed.text
    (run,) = listed.json()
    # The run is org_b's to see; the value behind it is not.
    assert run["event_object_id"] == observable.json()["id"]
    assert run["observable_value"] is None
    assert run["observable_type"] is None


async def test_manual_plugin_run_for_observable(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a, session,
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

    # The manual envelope must carry the observable snapshot on `event.data`,
    # same shape as the `observable.created` event — a handler wired to both
    # triggers (which reads event.data["observable_type"]) errors on an empty
    # payload otherwise. Regression for R-c9fc38 (InputError: observable event
    # has no observable_type).
    from app.models.plugin_runner import PluginEventDelivery

    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(
                PluginEventDelivery.event_id == data["event_id"]
            )
        )
    ).scalar_one()
    assert delivery.envelope["data"] == {
        "observable_type": "ip",
        "data": "1.2.3.4",
        "ioc": False,
    }

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


async def test_manual_plugin_run_cannot_target_another_orgs_observable(
    client: AsyncClient, runner_secret, admin_token, org_a, analyst_a_token,
    org_b, analyst_b_token,
):
    """Manual runs resolve observable visibility like the read routes: org B
    cannot trigger a run (enrichment spend + result writes) against org A's
    observable by guessing its UUID — it 404s exactly like the read would."""
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    for org_id in (org_a.id, org_b.id):
        await _enable_plugin_for_org(client, admin_token, org_id, plugin_id)

    h_a = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "IDOR", "description": ""}, headers=h_a
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "9.9.9.9", "tlp": 2, "pap": 2},
        headers=h_a,
    )
    obs_id = observable.json()["id"]

    for path, payload in (
        (f"/api/v1/observables/{obs_id}/plugin-runs", {"plugin_id": plugin_id}),
        (
            f"/api/v1/plugins/{plugin_id}/run",
            {"entity_type": "observable", "entity_id": obs_id},
        ),
    ):
        r = await client.post(path, json=payload, headers=_h(analyst_b_token, org_b.id))
        assert r.status_code == 404, r.text

    # The owner org can still run it.
    r = await client.post(
        f"/api/v1/observables/{obs_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h_a,
    )
    assert r.status_code == 200, r.text


# --- Manual run: case + alert entities (responders) ---


async def _make_alert(client, h, source_ref="evt-manual"):
    r = await client.post(
        "/api/v1/alerts/",
        json={
            "type": "phishing",
            "source": "mail-gw",
            "source_ref": source_ref,
            "title": "Manual run alert",
            "description": "",
            "severity": 2,
        },
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_manual_plugin_run_for_case(
    client: AsyncClient, session, runner_secret, admin_token, analyst_a_token, org_a,
):
    """A manual run against a CASE dispatches a targeted, case-shaped envelope so a
    responder (mailer) parses it: object.type=='case', object.id==<case id>, the
    manual flag + target_plugin_id are set. The 422 that once rejected non-observable
    entities is gone."""
    from app.models.plugin_runner import PluginEventDelivery

    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "Responder", "description": ""}, headers=h
    )
    case_id = case.json()["id"]

    r = await client.post(
        f"/api/v1/cases/{case_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["plugin_id"] == plugin_id
    assert data["event_type"] == "case.manual"
    assert data["event_object_type"] == "case"
    assert data["event_object_id"] == str(case_id)
    assert data["status"] == "queued"

    # The delivery the runner will claim is targeted + shaped like a case.created
    # envelope so mailer's `event.object_type == "case"` / `int(event.object_id)`
    # parse succeeds.
    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(
                PluginEventDelivery.event_id == data["event_id"]
            )
        )
    ).scalars().first()
    assert delivery is not None
    env = delivery.envelope
    assert env["target_plugin_id"] == plugin_id
    assert env["manual"] is True
    assert env["object"] == {"type": "case", "id": str(case_id)}

    # Deterministic id: a duplicate submit resolves to the same run identity.
    again = await client.post(
        f"/api/v1/cases/{case_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    assert again.status_code == 200, again.text
    assert again.json()["event_id"] == data["event_id"]


async def test_manual_plugin_run_for_alert(
    client: AsyncClient, session, runner_secret, admin_token, analyst_a_token, org_a,
):
    """A manual run against an ALERT dispatches a targeted alert-shaped envelope."""
    from app.models.plugin_runner import PluginEventDelivery

    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    alert_id = await _make_alert(client, h)

    r = await client.post(
        f"/api/v1/alerts/{alert_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["event_type"] == "alert.manual"
    assert data["event_object_type"] == "alert"
    assert data["event_object_id"] == str(alert_id)
    assert data["status"] == "queued"

    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(
                PluginEventDelivery.event_id == data["event_id"]
            )
        )
    ).scalars().first()
    assert delivery is not None
    env = delivery.envelope
    assert env["target_plugin_id"] == plugin_id
    assert env["manual"] is True
    assert env["object"] == {"type": "alert", "id": str(alert_id)}


async def test_manual_run_case_alert_no_longer_422(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    """The generic /plugins/{id}/run endpoint accepts case + alert now (the old
    observable-only 422 is gone)."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "No 422", "description": ""}, headers=h
    )
    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/run",
        json={"entity_type": "case", "entity_id": str(case.json()["id"])},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["event_type"] == "case.manual"


async def test_manual_run_rejects_unknown_entity_type(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    """An entity type outside {observable, case, alert} is still a 422."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/plugins/{plugin_id}/run",
        json={"entity_type": "task", "entity_id": "1"},
        headers=h,
    )
    assert r.status_code == 422, r.text


async def test_manual_run_case_visibility_enforced(
    client: AsyncClient, runner_secret, admin_token, org_a, analyst_a_token,
    org_b, analyst_b_token,
):
    """Org B cannot trigger a run against a case it can't see — it 404s like the
    read would, via both the dedicated and generic endpoints."""
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    for org_id in (org_a.id, org_b.id):
        await _enable_plugin_for_org(client, admin_token, org_id, plugin_id)

    h_a = _h(analyst_a_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "Private", "description": ""}, headers=h_a
    )
    case_id = case.json()["id"]

    for path, payload in (
        (f"/api/v1/cases/{case_id}/plugin-runs", {"plugin_id": plugin_id}),
        (
            f"/api/v1/plugins/{plugin_id}/run",
            {"entity_type": "case", "entity_id": str(case_id)},
        ),
    ):
        r = await client.post(path, json=payload, headers=_h(analyst_b_token, org_b.id))
        assert r.status_code == 404, (path, r.text)

    # The owner org can run it.
    ok = await client.post(
        f"/api/v1/cases/{case_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h_a,
    )
    assert ok.status_code == 200, ok.text


async def test_manual_run_case_requires_run_enrichment(
    client: AsyncClient, session, runner_secret, admin_token, org_a, readonly_a_token,
):
    """The read-only role (read:case but not run:enrichment) is 403."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    admin_h = _h(admin_token, org_a.id)
    case = await client.post(
        "/api/v1/cases/", json={"title": "Gate", "description": ""}, headers=admin_h
    )
    r = await client.post(
        f"/api/v1/cases/{case.json()['id']}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=_h(readonly_a_token, org_a.id),
    )
    assert r.status_code == 403, r.text


# --- Runnable catalog (GET /plugins/runnable) ---


RESPONDER_MANIFEST = {
    "id": "case-responder",
    "name": "Case Responder",
    "version": "0.1.0",
    "sdk": ">=0.1,<1",
    "runtime": "python",
    "entrypoint": "case_responder.plugin:Plugin",
    "capabilities": ["responder"],
    "triggers": ["case.created"],
    "permissions": ["read:case", "write:plugin_result"],
    "timeout_seconds": 30,
}


async def test_runnable_capability_responder_returns_responder_plugin(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """`runnable?capability=responder` returns a responder-capable plugin (mailer's
    manifest is the model). Capabilities are stored on the API-side plugin record,
    so the filter handles 'responder', not only 'enrichment'."""
    from tests.test_api_plugin_runners import RUNNER1, _register_runner

    await _register_runner(
        client, admin_token, RUNNER1, plugins=[RESPONDER_MANIFEST]
    )
    await _enable_plugin_for_org(
        client, admin_token, org_a.id, RESPONDER_MANIFEST["id"]
    )
    h = _h(admin_token, org_a.id)

    responders = await client.get(
        "/api/v1/plugins/runnable?capability=responder", headers=h
    )
    assert responders.status_code == 200, responders.text
    ids = [p["id"] for p in responders.json()]
    assert ids == [RESPONDER_MANIFEST["id"]]
    assert responders.json()[0]["capabilities"] == ["responder"]

    # The enrichment filter must NOT surface the responder.
    enrich = await client.get(
        "/api/v1/plugins/runnable?capability=enrichment", headers=h
    )
    assert enrich.status_code == 200
    assert RESPONDER_MANIFEST["id"] not in [p["id"] for p in enrich.json()]



async def _run_only_token(session, org_id, email="run-only@test.com"):
    """A token whose role can run enrichment (via the manage:org grant). This is
    exactly the analyst the runnable endpoint exists for."""
    from app.core.security import TokenPayload, create_access_token
    from app.crud.organisation_member import add_member
    from app.crud.role import create_role
    from app.crud.user import create_user
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.role import Permission, RoleCreate
    from app.models.user import UserCreate

    user = await create_user(
        session,
        UserCreate(
            first_name="Run", last_name="Only", email=email, password="password123"
        ),
    )
    role = await create_role(
        session,
        RoleCreate(name="run-only", permissions=[Permission.manage_org]),
        organisation_id=org_id,
        created_by="system",
    )
    await add_member(
        session,
        org_id,
        OrganisationMemberCreate(user_id=user.id, role_id=role.id),
        created_by="system",
    )
    return create_access_token(
        TokenPayload(user_id=user.id, is_superadmin=False, organisations=[org_id])
    )


async def _setup_runnable_plugin(
    client: AsyncClient, admin_token: str, org_id: str, runner_id: str = "runner-1",
) -> tuple[str, str]:
    """Enabled + config-complete plugin on a healthy+enrolled runner — the full bar
    the runnable picker requires (SAMPLE_MANIFEST declares a required api_key secret,
    so config_complete needs it stored)."""
    plugin_id, credential = await _setup_enabled_plugin(
        client, admin_token, org_id, runner_id
    )
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=_h(admin_token, org_id),
    )
    return plugin_id, credential


async def test_runnable_lists_only_eligible_plugin(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """An enabled + installed + configured plugin on a healthy runner is runnable;
    the slim shape carries the manifest capabilities."""
    plugin_id, _ = await _setup_runnable_plugin(client, admin_token, org_a.id)
    h = _h(admin_token, org_a.id)

    r = await client.get("/api/v1/plugins/runnable", headers=h)
    assert r.status_code == 200, r.text
    runnable = r.json()
    assert len(runnable) == 1
    assert runnable[0] == {
        "id": plugin_id,
        "name": "Acme Threat Intel",
        "description": "",
        "capabilities": ["enrichment"],
    }


async def test_runnable_excludes_disabled_plugin(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """Installed but not org-enabled → not runnable (would 409 on a manual run)."""
    await _setup_runner_and_plugin(client, admin_token)  # registered, not enabled
    h = _h(admin_token, org_a.id)

    r = await client.get("/api/v1/plugins/runnable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_runnable_excludes_no_active_version_and_not_installed(
    client: AsyncClient, session, admin_token, org_a,
):
    """Enabled plugins that (a) have no active version or (b) have an active
    version installed on no runner are excluded — the same conditions that make a
    manual run 409."""
    from app.models.plugin_runner import OrgPlugin, PluginDefinition, PluginVersion

    # (a) enabled, no active version
    session.add(PluginDefinition(id="no-active-version", display_name="No Version"))
    # (b) enabled, active version but installed on no runner. The version row must
    # exist before the definition's active_version_id FK references it.
    session.add(PluginDefinition(id="uninstalled", display_name="Uninstalled"))
    session.add(PluginVersion(id="uninstalled@1.0.0", plugin_id="uninstalled", version="1.0.0"))
    await session.flush()
    uninstalled = await session.get(PluginDefinition, "uninstalled")
    uninstalled.active_version_id = "uninstalled@1.0.0"
    await session.flush()
    for pid in ("no-active-version", "uninstalled"):
        session.add(OrgPlugin(organisation_id=org_a.id, plugin_id=pid, enabled=True))
    await session.commit()

    r = await client.get("/api/v1/plugins/runnable", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_runnable_capability_filter(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runnable_plugin(client, admin_token, org_a.id)
    h = _h(admin_token, org_a.id)

    match = await client.get("/api/v1/plugins/runnable?capability=enrichment", headers=h)
    assert match.status_code == 200
    assert [p["id"] for p in match.json()] == [plugin_id]

    miss = await client.get("/api/v1/plugins/runnable?capability=responder", headers=h)
    assert miss.status_code == 200
    assert miss.json() == []


async def test_runnable_excludes_config_incomplete_plugin(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """Enabled + installed on a healthy runner but a required secret is unset →
    excluded (it would accept a run yet never enrich)."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(admin_token, org_a.id)

    # SAMPLE_MANIFEST requires api_key; unset → not runnable.
    r = await client.get("/api/v1/plugins/runnable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []

    # Once configured, it becomes runnable.
    await client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        json={"settings": {}, "secrets": {"api_key": "s3cr3t"}},
        headers=h,
    )
    r = await client.get("/api/v1/plugins/runnable", headers=h)
    assert [p["id"] for p in r.json()] == [plugin_id]


async def test_runnable_excludes_plugin_on_unhealthy_runner(
    client: AsyncClient, session, runner_secret, admin_token, org_a,
):
    """A plugin installed only on an unhealthy runner dispatches zero deliveries,
    so it is excluded from runnable even though a manual run would not 409."""
    from app.models.plugin_runner import PluginRunner

    plugin_id, _ = await _setup_runnable_plugin(client, admin_token, org_a.id)
    h = _h(admin_token, org_a.id)
    assert [p["id"] for p in (await client.get("/api/v1/plugins/runnable", headers=h)).json()] == [plugin_id]

    # Take the only runner offline; no healthy+enrolled delivery target remains.
    runner = await session.get(PluginRunner, "runner-1")
    runner.status = "offline"
    await session.commit()

    r = await client.get("/api/v1/plugins/runnable", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_runnable_gated_on_run_enrichment_not_connector_read(
    client: AsyncClient, session, runner_secret, admin_token, org_a, readonly_a_token,
):
    """A role with run:enrichment (via manage:org) succeeds; the read-only role
    (no run:enrichment) is 403 — the gate is run:enrichment."""
    plugin_id, _ = await _setup_runnable_plugin(client, admin_token, org_a.id)
    run_only = await _run_only_token(session, org_a.id)

    ok = await client.get("/api/v1/plugins/runnable", headers=_h(run_only, org_a.id))
    assert ok.status_code == 200, ok.text
    assert [p["id"] for p in ok.json()] == [plugin_id]

    # read-only has no run:enrichment → 403.
    denied = await client.get(
        "/api/v1/plugins/runnable", headers=_h(readonly_a_token, org_a.id)
    )
    assert denied.status_code == 403


# --- Manual run: force flag ---


async def _make_observable(client, h):
    case = await client.post(
        "/api/v1/cases/", json={"title": "Force", "description": ""}, headers=h
    )
    observable = await client.post(
        f"/api/v1/cases/{case.json()['id']}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4", "tlp": 2, "pap": 2},
        headers=h,
    )
    return observable.json()["id"]


async def test_force_false_dedupes_second_run(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    """Default force=False keeps the deterministic event id: a re-run resolves to
    the same run identity (silent dedup, unchanged behaviour)."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    obs_id = await _make_observable(client, h)

    first = await client.post(
        f"/api/v1/observables/{obs_id}/plugin-runs",
        json={"plugin_id": plugin_id, "force": False},
        headers=h,
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"/api/v1/observables/{obs_id}/plugin-runs",
        json={"plugin_id": plugin_id},  # force omitted → defaults False
        headers=h,
    )
    assert second.status_code == 200, second.text
    assert second.json()["event_id"] == first.json()["event_id"]
    assert second.json()["id"] == first.json()["id"]


async def test_force_true_creates_distinct_run(
    client: AsyncClient, runner_secret, admin_token, analyst_a_token, org_a,
):
    """force=True salts the event id with a time component, so a real re-run
    dispatches instead of deduping."""
    plugin_id, _ = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _h(analyst_a_token, org_a.id)
    obs_id = await _make_observable(client, h)

    base = await client.post(
        f"/api/v1/observables/{obs_id}/plugin-runs",
        json={"plugin_id": plugin_id},
        headers=h,
    )
    forced = await client.post(
        f"/api/v1/observables/{obs_id}/plugin-runs",
        json={"plugin_id": plugin_id, "force": True},
        headers=h,
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["event_id"] != base.json()["event_id"]
    assert forced.json()["id"] != base.json()["id"]


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


async def test_list_runs_filters_by_status_plugin_and_runner(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """The runs list applies exact-match status/plugin/runner filters server-side
    so they narrow the whole dataset, not just a fetched page."""
    plugin_id, credential = await _setup_enabled_plugin(client, admin_token, org_a.id)
    h = _internal_h(credential)
    await client.post(
        "/api/internal/plugin-runner/runs",
        json={
            "event_id": "filter:1",
            "event_type": "observable.created",
            "organisation_id": org_a.id,
            "plugin_id": plugin_id,
            "plugin_version": "1.2.0",
            "runner_id": "runner-1",
            "trigger_metadata": {},
        },
        headers=h,
    )
    oh = _h(admin_token, org_a.id)

    async def ids(query: str) -> list[str]:
        r = await client.get(f"/api/v1/plugin-runs{query}", headers=oh)
        assert r.status_code == 200, r.text
        return [run["plugin_id"] for run in r.json()]

    # A newly created run is queued on runner-1 for this plugin.
    assert await ids("") == [plugin_id]
    assert await ids(f"?plugin_id={plugin_id}") == [plugin_id]
    assert await ids(f"?status=queued&plugin_id={plugin_id}") == [plugin_id]
    assert await ids("?runner_id=runner-1") == [plugin_id]
    # Non-matching filters return nothing.
    assert await ids("?plugin_id=does-not-exist") == []
    assert await ids("?status=success") == []
    assert await ids("?runner_id=runner-2") == []


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
    """A plugin's observable patch is a proposed canonical edit (202), not a
    direct write — an analyst approves it before the IOC/message change lands.
    (Full propose->approve->applies coverage lives in test_api_proposed_actions.)"""
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
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "proposed"
