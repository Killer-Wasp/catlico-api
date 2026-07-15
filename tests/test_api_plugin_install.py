"""Plugin INSTALL feature: admin trigger route + internal install-status sink."""
import json

import httpx
import pytest
from httpx import AsyncClient

from app.api.v1.routes import plugin_runners as pr_routes
from app.models.plugin_runner import PluginRunner as PluginRunnerModel
from app.services.plugin_dispatch import _signature
from tests.test_api_plugin_runners import RUNNER_SECRET, _register_runner


def _admin_h(token):
    return {"Authorization": f"Bearer {token}"}


def _internal_h(secret, runner_id="runner-1"):
    return {"Authorization": f"Bearer {secret}", "X-Runner-Id": runner_id}


async def _create_runner(client: AsyncClient, admin_token: str | None = None, runner_id="runner-1"):
    """Self-register a runner (advertising its base_url) with the shared secret."""
    r = await client.post(
        "/api/internal/plugin-runner/register",
        json={"id": runner_id, "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
    )
    assert r.status_code == 200, r.text


# --- (a) Public trigger route -------------------------------------------------


async def test_install_trigger_happy_path(client: AsyncClient, admin_token, session, monkeypatch):
    await _create_runner(client, admin_token)

    calls = []

    async def fake_post_signed(runner, path, payload, *, transport=None):
        calls.append((runner.id, runner.base_url, path, payload))
        return {"accepted": True}

    monkeypatch.setattr(pr_routes, "_runner_post_signed", fake_post_signed)

    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={
            "plugin_id": "acme-intel",
            "source_url": "https://github.com/acme/intel",
            "source_ref": "main",
        },
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 202, r.text
    data = r.json()
    assert data == {"plugin_version_id": "acme-intel@main", "status": "installing"}

    # The runner was asked to install, with the exact URL path + body contract.
    assert len(calls) == 1
    runner_id, base_url, path, payload = calls[0]
    assert runner_id == "runner-1"
    assert base_url == "http://runner:8080"
    assert path == "/internal/plugins/install"
    assert payload == {
        "plugin_version_id": "acme-intel@main",
        "plugin_id": "acme-intel",
        "source_url": "https://github.com/acme/intel",
        "source_ref": "main",
    }

    # Catalog rows exist in the installing/pending state.
    from app.models.plugin_runner import (
        PluginDefinition,
        PluginVersion,
        RunnerPluginInstallation,
    )

    pdef = await session.get(PluginDefinition, "acme-intel")
    assert pdef is not None and pdef.display_name == "acme-intel"
    pver = await session.get(PluginVersion, "acme-intel@main")
    assert pver is not None
    assert pver.status == "installing"
    assert pver.source_type == "github"
    assert pver.source_url == "https://github.com/acme/intel"
    assert pver.source_ref == "main"
    assert pver.commit_sha == ""
    inst = await session.get(RunnerPluginInstallation, ("runner-1", "acme-intel@main"))
    assert inst is not None and inst.install_status == "pending"


async def test_install_trigger_uses_version_when_supplied(
    client: AsyncClient, admin_token, monkeypatch
):
    await _create_runner(client, admin_token)

    captured = {}

    async def fake_post_signed(runner, path, payload, *, transport=None):
        captured.update(payload)
        return {"accepted": True}

    monkeypatch.setattr(pr_routes, "_runner_post_signed", fake_post_signed)

    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={
            "plugin_id": "acme-intel",
            "source_url": "https://github.com/acme/intel",
            "source_ref": "release",
            "version": "2.0.0",
        },
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 202, r.text
    assert r.json()["plugin_version_id"] == "acme-intel@2.0.0"
    assert captured["plugin_version_id"] == "acme-intel@2.0.0"


async def test_install_trigger_unknown_runner_404(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/plugin-runners/ghost/plugins/install",
        json={"plugin_id": "p", "source_url": "https://x", "source_ref": "main"},
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 404


async def test_install_trigger_requires_superadmin(
    client: AsyncClient, admin_token, org_a, analyst_a_token
):
    await _create_runner(client, admin_token)
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={"plugin_id": "p", "source_url": "https://x", "source_ref": "main"},
        headers={"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id},
    )
    assert r.status_code == 403


async def test_install_trigger_runner_failure_502(
    client: AsyncClient, admin_token, monkeypatch
):
    await _create_runner(client, admin_token)

    async def boom(runner, path, payload, *, transport=None):
        raise httpx.ConnectError("no route to runner")

    monkeypatch.setattr(pr_routes, "_runner_post_signed", boom)

    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={"plugin_id": "acme-intel", "source_url": "https://x", "source_ref": "main"},
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 502, r.text


async def test_runner_post_signed_signs_body_with_push_secret():
    """The POST helper attaches an x-catlico-signature computed over the raw body
    with the shared secret — the same scheme /internal/events uses."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["sig"] = request.headers.get("x-catlico-signature")
        captured["ct"] = request.headers.get("content-type")
        return httpx.Response(200, json={"accepted": True})

    transport = httpx.MockTransport(handler)
    runner = PluginRunnerModel(id="runner-1", base_url="http://runner:8080")
    payload = {"plugin_version_id": "acme-intel@main", "plugin_id": "acme-intel"}
    result = await pr_routes._runner_post_signed(
        runner, "/internal/plugins/install", payload, transport=transport
    )
    assert result == {"accepted": True}
    assert captured["url"] == "http://runner:8080/internal/plugins/install"
    expected_body = json.dumps(payload).encode()
    assert captured["body"] == expected_body
    assert captured["ct"] == "application/json"
    assert captured["sig"] == _signature(expected_body, RUNNER_SECRET)


async def test_runner_post_signed_non_2xx_raises():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"e": "x"}))
    runner = PluginRunnerModel(id="runner-1", base_url="http://runner:8080")
    with pytest.raises(httpx.HTTPStatusError):
        await pr_routes._runner_post_signed(
            runner, "/internal/plugins/install", {"a": 1}, transport=transport
        )


# --- (b) Internal install-status sink ----------------------------------------


async def _trigger_install(client, admin_token, monkeypatch, plugin_id="acme-intel"):
    await _create_runner(client, admin_token)

    async def noop(runner, path, payload, *, transport=None):
        return {"accepted": True}

    monkeypatch.setattr(pr_routes, "_runner_post_signed", noop)
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={"plugin_id": plugin_id, "source_url": "https://x", "source_ref": "main"},
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 202, r.text
    return r.json()["plugin_version_id"]


async def test_install_status_building_then_installed(
    client: AsyncClient, admin_token, session, monkeypatch
):
    version_id = await _trigger_install(client, admin_token, monkeypatch)
    # _trigger_install already self-registered runner-1; internal calls auth with
    # the shared secret + X-Runner-Id.
    h = _internal_h(RUNNER_SECRET)

    # building -> installing
    r = await client.post(
        f"/api/internal/plugin-runner/plugins/{version_id}/install-status",
        json={"state": "building"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "installing"}

    # installed -> installed + commit_sha
    r = await client.post(
        f"/api/internal/plugin-runner/plugins/{version_id}/install-status",
        json={"state": "installed", "commit_sha": "abc123", "image_digest": "sha256:deadbeef"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "installed"}

    from app.models.plugin_runner import PluginVersion, RunnerPluginInstallation

    await session.refresh(await session.get(PluginVersion, version_id))
    pver = await session.get(PluginVersion, version_id)
    assert pver.status == "installed"
    assert pver.commit_sha == "abc123"
    assert pver.image_digest == "sha256:deadbeef"
    inst = await session.get(RunnerPluginInstallation, ("runner-1", version_id))
    await session.refresh(inst)
    assert inst.install_status == "installed"


async def test_install_status_failed(client: AsyncClient, admin_token, session, monkeypatch):
    version_id = await _trigger_install(client, admin_token, monkeypatch)
    h = _internal_h(RUNNER_SECRET)

    r = await client.post(
        f"/api/internal/plugin-runner/plugins/{version_id}/install-status",
        json={"state": "failed", "error": "build blew up", "install_log": "step 3 failed"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"

    from app.models.plugin_runner import PluginVersion

    pver = await session.get(PluginVersion, version_id)
    await session.refresh(pver)
    assert pver.status == "failed"
    assert "build blew up" in (pver.install_log or "")


async def test_install_status_unknown_installation_404(
    client: AsyncClient, admin_token
):
    """A runner with no installation row for the version gets 404."""
    _, credential = await _register_runner(client, admin_token)
    h = _internal_h(credential)
    r = await client.post(
        "/api/internal/plugin-runner/plugins/ghost@main/install-status",
        json={"state": "building"},
        headers=h,
    )
    assert r.status_code == 404, r.text


async def test_install_status_rejects_other_runners_installation(
    client: AsyncClient, admin_token, monkeypatch
):
    """Runner B cannot report status for a version installed on runner A."""
    version_id = await _trigger_install(client, admin_token, monkeypatch)

    # Enroll a DIFFERENT runner (runner-2) which does NOT host this version.
    from tests.test_api_plugin_runners import RUNNER1

    _, credential_b = await _register_runner(
        client, admin_token, {**RUNNER1, "id": "runner-2", "name": "Runner Two"}
    )
    r = await client.post(
        f"/api/internal/plugin-runner/plugins/{version_id}/install-status",
        json={"state": "building"},
        headers=_internal_h(credential_b, "runner-2"),
    )
    assert r.status_code == 404, r.text


async def test_install_status_requires_runner_auth(client: AsyncClient):
    r = await client.post(
        "/api/internal/plugin-runner/plugins/x@main/install-status",
        json={"state": "building"},
    )
    assert r.status_code == 401
