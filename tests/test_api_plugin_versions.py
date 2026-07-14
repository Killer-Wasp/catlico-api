"""Public API: plugin version metadata for the Versions tab.

Read surface the web Versions tab consumes:
  * GET /plugins/{plugin_id}/versions        -> installed version + runners
  * GET /plugins/{plugin_id}/versions/check-latest -> best-effort latest check
"""
from datetime import UTC, datetime

from httpx import AsyncClient

from app.api.v1.routes import plugins as plugins_routes
from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _register_runner,
)
from tests.test_api_plugins_public import _run_only_token, _setup_runner_and_plugin


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


# --- GET /plugins/{plugin_id}/versions ---------------------------------------


async def test_versions_returns_installed_metadata_and_runners(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.get(
        f"/api/v1/plugins/{plugin_id}/versions", headers=_h(admin_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["plugin_id"] == plugin_id
    assert data["installed_version"] == SAMPLE_MANIFEST["version"]  # "1.2.0"
    assert data["installed_version_id"] == f"{plugin_id}@{SAMPLE_MANIFEST['version']}"
    assert data["installed_at"] is not None
    # The plugin is installed on exactly runner-1, with its name + health status.
    assert len(data["runners"]) == 1
    runner = data["runners"][0]
    assert runner["id"] == "runner-1"
    assert runner["name"] == "Test Runner"
    assert runner["status"] == "healthy"
    assert runner["install_status"] == "installed"


async def test_versions_unknown_plugin_404(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get(
        "/api/v1/plugins/ghost/versions", headers=_h(admin_token, org_a.id)
    )
    assert r.status_code == 404


async def test_versions_requires_read_connector(
    client: AsyncClient, session, runner_secret, admin_token, org_a,
):
    """The metadata endpoint is gated on read:connector (same as plugin detail).
    A run:enrichment-only token has no read:connector and is rejected."""
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    run_only = await _run_only_token(session, org_a.id, email="ver-run-only@test.com")

    r = await client.get(
        f"/api/v1/plugins/{plugin_id}/versions", headers=_h(run_only, org_a.id)
    )
    assert r.status_code == 403


async def test_versions_no_active_version_returns_nulls(
    client: AsyncClient, session, admin_token, org_a,
):
    """A catalog plugin with no installed version reports installed_version null
    and an empty runners list (rather than 404) so the tab can render 'not
    installed'."""
    from app.models.plugin_runner import PluginDefinition

    session.add(PluginDefinition(id="uninstalled", display_name="Uninstalled"))
    await session.commit()

    r = await client.get(
        "/api/v1/plugins/uninstalled/versions", headers=_h(admin_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] is None
    assert data["runners"] == []


async def test_versions_surfaces_source_ref_from_install(
    client: AsyncClient, session, admin_token, org_a,
):
    """When a version was installed from a git source, source_url/source_ref are
    surfaced. Build the state directly: an installed RunnerPluginInstallation is
    the source of truth even when active_version_id is not (yet) promoted."""
    from app.models.plugin_runner import (
        PluginDefinition,
        PluginRunner,
        PluginVersion,
        RunnerPluginInstallation,
    )

    now = datetime.now(UTC)
    session.add(PluginRunner(id="runner-src", name="Src Runner", status="healthy"))
    session.add(PluginDefinition(id="srcplugin", display_name="Src Plugin"))
    session.add(
        PluginVersion(
            id="srcplugin@2.1.0",
            plugin_id="srcplugin",
            version="2.1.0",
            source_type="github",
            source_url="https://github.com/acme/srcplugin",
            source_ref="v2.1.0",
            commit_sha="deadbeef",
            installed_at=now,
            status="installed",
        )
    )
    await session.flush()
    session.add(
        RunnerPluginInstallation(
            runner_id="runner-src",
            plugin_version_id="srcplugin@2.1.0",
            install_status="installed",
            installed_at=now,
        )
    )
    await session.commit()

    r = await client.get(
        "/api/v1/plugins/srcplugin/versions", headers=_h(admin_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] == "2.1.0"
    assert data["source_url"] == "https://github.com/acme/srcplugin"
    assert data["source_ref"] == "v2.1.0"
    assert data["commit_sha"] == "deadbeef"
    assert [rr["id"] for rr in data["runners"]] == ["runner-src"]


# --- GET /plugins/{plugin_id}/versions/check-latest --------------------------


async def test_check_latest_unknown_when_no_source_ref(
    client: AsyncClient, runner_secret, admin_token, org_a,
):
    """Default (register/sync) install has no source ref -> the check cannot
    determine a latest version and returns a well-formed 'unknown' state, never
    an error."""
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)

    r = await client.get(
        f"/api/v1/plugins/{plugin_id}/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] == SAMPLE_MANIFEST["version"]
    assert data["latest_version"] is None
    assert data["update_available"] is False
    assert data["status"] == "unknown"
    assert data["reason"] == "no_source_ref"


async def test_check_latest_unknown_when_plugin_not_installed(
    client: AsyncClient, session, admin_token, org_a,
):
    """A catalog plugin with no installation at all -> 'unknown' with the
    not_installed reason (installed_version is null)."""
    from app.models.plugin_runner import PluginDefinition

    session.add(PluginDefinition(id="notinstalled", display_name="Not Installed"))
    await session.commit()

    r = await client.get(
        "/api/v1/plugins/notinstalled/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] is None
    assert data["latest_version"] is None
    assert data["update_available"] is False
    assert data["status"] == "unknown"
    assert data["reason"] == "not_installed"


async def test_check_latest_unknown_when_source_returns_no_version(
    client: AsyncClient, session, admin_token, org_a,
):
    """The live v1 path: a plugin WITH a source_ref, but the (default-stub)
    source lookup yields no version -> 'unknown' with source_check_unavailable.
    No monkeypatch here: this exercises the real _fetch_source_latest_version."""
    from app.models.plugin_runner import (
        PluginDefinition, PluginRunner, PluginVersion, RunnerPluginInstallation,
    )

    now = datetime.now(UTC)
    session.add(PluginRunner(id="runner-live", name="Live", status="healthy"))
    session.add(PluginDefinition(id="liveplugin", display_name="Live Plugin"))
    session.add(
        PluginVersion(
            id="liveplugin@1.0.0", plugin_id="liveplugin", version="1.0.0",
            source_url="https://github.com/acme/live", source_ref="main",
            installed_at=now, status="installed",
        )
    )
    await session.flush()
    session.add(
        RunnerPluginInstallation(
            runner_id="runner-live", plugin_version_id="liveplugin@1.0.0",
            install_status="installed", installed_at=now,
        )
    )
    await session.commit()

    r = await client.get(
        "/api/v1/plugins/liveplugin/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] == "1.0.0"
    assert data["latest_version"] is None
    assert data["update_available"] is False
    assert data["status"] == "unknown"
    assert data["reason"] == "source_check_unavailable"


async def test_check_latest_reports_update_available(
    client: AsyncClient, session, admin_token, org_a, monkeypatch,
):
    """When the source reports a newer version than installed, the check flags
    update_available with status 'update_available'."""
    from app.models.plugin_runner import (
        PluginDefinition,
        PluginRunner,
        PluginVersion,
        RunnerPluginInstallation,
    )

    now = datetime.now(UTC)
    session.add(PluginRunner(id="runner-u", name="U", status="healthy"))
    session.add(PluginDefinition(id="uplugin", display_name="U Plugin"))
    session.add(
        PluginVersion(
            id="uplugin@1.0.0", plugin_id="uplugin", version="1.0.0",
            source_url="https://github.com/acme/u", source_ref="main",
            installed_at=now, status="installed",
        )
    )
    await session.flush()
    session.add(
        RunnerPluginInstallation(
            runner_id="runner-u", plugin_version_id="uplugin@1.0.0",
            install_status="installed", installed_at=now,
        )
    )
    await session.commit()

    async def fake_latest(source_url, source_ref):
        return "1.3.0"

    monkeypatch.setattr(plugins_routes, "_fetch_source_latest_version", fake_latest)

    r = await client.get(
        "/api/v1/plugins/uplugin/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["installed_version"] == "1.0.0"
    assert data["latest_version"] == "1.3.0"
    assert data["update_available"] is True
    assert data["status"] == "update_available"


async def test_check_latest_up_to_date_when_source_matches(
    client: AsyncClient, session, admin_token, org_a, monkeypatch,
):
    from app.models.plugin_runner import (
        PluginDefinition, PluginRunner, PluginVersion, RunnerPluginInstallation,
    )

    now = datetime.now(UTC)
    session.add(PluginRunner(id="runner-t", name="T", status="healthy"))
    session.add(PluginDefinition(id="tplugin", display_name="T Plugin"))
    session.add(
        PluginVersion(
            id="tplugin@2.0.0", plugin_id="tplugin", version="2.0.0",
            source_url="https://github.com/acme/t", source_ref="main",
            installed_at=now, status="installed",
        )
    )
    await session.flush()
    session.add(
        RunnerPluginInstallation(
            runner_id="runner-t", plugin_version_id="tplugin@2.0.0",
            install_status="installed", installed_at=now,
        )
    )
    await session.commit()

    async def fake_latest(source_url, source_ref):
        return "2.0.0"

    monkeypatch.setattr(plugins_routes, "_fetch_source_latest_version", fake_latest)

    r = await client.get(
        "/api/v1/plugins/tplugin/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["latest_version"] == "2.0.0"
    assert data["update_available"] is False
    assert data["status"] == "up_to_date"


async def test_check_latest_unknown_when_source_fetch_raises(
    client: AsyncClient, session, admin_token, org_a, monkeypatch,
):
    """A fetch failure must degrade to 'unknown', never error the request."""
    from app.models.plugin_runner import (
        PluginDefinition, PluginRunner, PluginVersion, RunnerPluginInstallation,
    )

    now = datetime.now(UTC)
    session.add(PluginRunner(id="runner-f", name="F", status="healthy"))
    session.add(PluginDefinition(id="fplugin", display_name="F Plugin"))
    session.add(
        PluginVersion(
            id="fplugin@1.0.0", plugin_id="fplugin", version="1.0.0",
            source_url="https://github.com/acme/f", source_ref="main",
            installed_at=now, status="installed",
        )
    )
    await session.flush()
    session.add(
        RunnerPluginInstallation(
            runner_id="runner-f", plugin_version_id="fplugin@1.0.0",
            install_status="installed", installed_at=now,
        )
    )
    await session.commit()

    async def boom(source_url, source_ref):
        raise RuntimeError("network down")

    monkeypatch.setattr(plugins_routes, "_fetch_source_latest_version", boom)

    r = await client.get(
        "/api/v1/plugins/fplugin/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["latest_version"] is None
    assert data["update_available"] is False
    assert data["status"] == "unknown"
    assert data["reason"] == "source_check_failed"


async def test_check_latest_unknown_plugin_404(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get(
        "/api/v1/plugins/ghost/versions/check-latest",
        headers=_h(admin_token, org_a.id),
    )
    assert r.status_code == 404


async def test_check_latest_requires_read_connector(
    client: AsyncClient, session, runner_secret, admin_token, org_a,
):
    plugin_id, _ = await _setup_runner_and_plugin(client, admin_token)
    run_only = await _run_only_token(
        session, org_a.id, email="ver-latest-run-only@test.com"
    )
    r = await client.get(
        f"/api/v1/plugins/{plugin_id}/versions/check-latest",
        headers=_h(run_only, org_a.id),
    )
    assert r.status_code == 403
