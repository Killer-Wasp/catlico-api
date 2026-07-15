"""Public API: plugin runner management (admin-only).

Runners self-register via the internal ``/register`` endpoint (there is no admin
create/enroll route any more); the admin API here only lists/inspects them and
drives health-check/sync/install.
"""
from httpx import AsyncClient

from tests.test_api_plugin_runners import RUNNER_SECRET, SAMPLE_MANIFEST


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _register(client: AsyncClient, runner_id="runner-1", *, base_url="http://runner:8080"):
    """Self-register a runner with the shared secret (advertising base_url)."""
    r = await client.post(
        "/api/internal/plugin-runner/register",
        json={"id": runner_id, "name": "Test Runner", "base_url": base_url},
        headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
    )
    assert r.status_code == 200, r.text


async def test_list_runners_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/plugin-runners")).status_code == 401


async def test_list_runners_empty(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get("/api/v1/plugin-runners", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_create_route_is_gone(client: AsyncClient, admin_token):
    """The token-minting admin create route was removed; runners self-register."""
    r = await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code in (404, 405)


async def test_list_shows_self_registered_runner(client: AsyncClient, admin_token, org_a):
    await _register(client)
    r = await client.get("/api/v1/plugin-runners", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == "runner-1"
    assert rows[0]["status"] == "healthy"


async def test_get_runner_by_id(
    client: AsyncClient, admin_token,
):
    await _register(client)
    r = await client.get(
        "/api/v1/plugin-runners/runner-1",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "runner-1"


async def test_get_unknown_runner_404(client: AsyncClient, admin_token):
    r = await client.get(
        "/api/v1/plugin-runners/ghost",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


async def test_health_check_endpoint(
    client: AsyncClient, admin_token, monkeypatch,
):
    async def fake_runner_get_json(base_url: str, path: str):
        assert base_url == "http://runner:8080"
        assert path == "/internal/health"
        return {
            "status": "ok",
            "version": "0.2.0",
            "capabilities": ["container"],
            "isolation_mode": "container",
        }

    monkeypatch.setattr(
        "app.api.v1.routes.plugin_runners._runner_get_json",
        fake_runner_get_json,
    )

    await _register(client)
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/health-check",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "healthy"
    assert data["health"]["version"] == "0.2.0"


async def test_health_check_unknown_runner_404(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/plugin-runners/ghost/health-check",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


async def test_sync_runner_fetches_plugin_inventory(
    client: AsyncClient, admin_token, org_a, monkeypatch,
):
    async def fake_runner_get_json(base_url: str, path: str):
        assert base_url == "http://runner:8080"
        assert path == "/internal/plugins"
        return {"plugins": [SAMPLE_MANIFEST]}

    monkeypatch.setattr(
        "app.api.v1.routes.plugin_runners._runner_get_json",
        fake_runner_get_json,
    )

    await _register(client)
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/sync",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["plugin_count"] == 1

    plugins = await client.get("/api/v1/plugins", headers=_h(admin_token, org_a.id))
    assert plugins.status_code == 200, plugins.text
    assert plugins.json()[0]["id"] == SAMPLE_MANIFEST["id"]
