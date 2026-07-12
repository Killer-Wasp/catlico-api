"""Public API: plugin runner management (admin-only)."""
from httpx import AsyncClient

from tests.test_api_plugin_runners import SAMPLE_MANIFEST


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def test_list_runners_requires_auth(client: AsyncClient):
    assert (await client.get("/api/v1/plugin-runners")).status_code == 401


async def test_list_runners_empty(
    client: AsyncClient, admin_token, org_a,
):
    r = await client.get("/api/v1/plugin-runners", headers=_h(admin_token, org_a.id))
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_superadmin_can_create_runner(
    client: AsyncClient, admin_token,
):
    body = {
        "id": "runner-1",
        "name": "Test Runner",
        "base_url": "http://runner:8080",
    }
    r = await client.post(
        "/api/v1/plugin-runners",
        json=body,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == "runner-1"
    assert data["name"] == "Test Runner"


async def test_non_superadmin_cannot_create_runner(
    client: AsyncClient, org_a, analyst_a_token,
):
    r = await client.post(
        "/api/v1/plugin-runners",
        json={"id": "r2", "name": "Nope", "base_url": "http://r"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 403


async def test_get_runner_by_id(
    client: AsyncClient, admin_token,
):
    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
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


async def test_re_enroll_mints_fresh_token(
    client: AsyncClient, admin_token,
):
    create = await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create.status_code == 200, create.text
    original_token = create.json()["enrollment_token"]

    r = await client.post(
        "/api/v1/plugin-runners/runner-1/re-enroll",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == "runner-1"
    assert data["enrollment_state"] == "pending"
    assert data["enrollment_token"]
    assert data["enrollment_token"] != original_token
    assert data["enrollment_token_expires_at"]


async def test_re_enroll_unknown_runner_404(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/plugin-runners/ghost/re-enroll",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404


async def test_non_superadmin_cannot_re_enroll(
    client: AsyncClient, org_a, analyst_a_token, admin_token,
):
    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/re-enroll",
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 403


async def test_re_enrolled_token_can_register(
    client: AsyncClient, admin_token,
):
    """The freshly minted token completes the runner-side register exchange."""
    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/re-enroll",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    token = r.json()["enrollment_token"]
    register = await client.post(
        "/api/internal/plugin-runner/register",
        json={
            "id": "runner-1",
            "name": "Test Runner",
            "version": "0.1.0",
            "capabilities": ["container"],
            "isolation_mode": "container",
            "enrollment_token": token,
        },
    )
    assert register.status_code == 200, register.text
    assert register.json()["runner_credential"].startswith("cpr_")


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

    # Create a runner first
    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
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

    await client.post(
        "/api/v1/plugin-runners",
        json={"id": "runner-1", "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/sync",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["plugin_count"] == 1

    plugins = await client.get("/api/v1/plugins", headers=_h(admin_token, org_a.id))
    assert plugins.status_code == 200, plugins.text
    assert plugins.json()[0]["id"] == SAMPLE_MANIFEST["id"]
