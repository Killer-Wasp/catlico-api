"""Plugin install route removal (venv redesign WP4).

Runtime plugin install is gone: plugins are provisioned into the runner's
``/plugins`` dir at image build. The public trigger route now returns 410 Gone
and the internal install-status sink has been deleted (no route).
"""
from httpx import AsyncClient

from tests.test_api_plugin_runners import RUNNER_SECRET


def _admin_h(token):
    return {"Authorization": f"Bearer {token}"}


def _internal_h(secret, runner_id="runner-1"):
    return {"Authorization": f"Bearer {secret}", "X-Runner-Id": runner_id}


async def _create_runner(client: AsyncClient, runner_id="runner-1"):
    """Self-register a runner (advertising its base_url) with the shared secret."""
    r = await client.post(
        "/api/internal/plugin-runner/register",
        json={"id": runner_id, "name": "Test Runner", "base_url": "http://runner:8080"},
        headers={"Authorization": f"Bearer {RUNNER_SECRET}"},
    )
    assert r.status_code == 200, r.text


# --- (a) Public trigger route is gone (410) ----------------------------------


async def test_install_route_returns_410(client: AsyncClient, admin_token):
    await _create_runner(client)
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={
            "plugin_id": "acme-intel",
            "source_url": "https://github.com/acme/intel",
            "source_ref": "main",
        },
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 410, r.text
    assert "provisioned" in r.json()["detail"].lower()


async def test_install_route_410_even_for_unknown_runner(client: AsyncClient, admin_token):
    # The route no longer touches the runner row — it always 410s.
    r = await client.post(
        "/api/v1/plugin-runners/ghost/plugins/install",
        json={"plugin_id": "p"},
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 410, r.text


async def test_install_route_still_requires_superadmin(
    client: AsyncClient, org_a, analyst_a_token
):
    r = await client.post(
        "/api/v1/plugin-runners/runner-1/plugins/install",
        json={"plugin_id": "p"},
        headers={"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id},
    )
    assert r.status_code == 403


# --- (b) Internal install-status sink is deleted -----------------------------


async def test_install_status_sink_removed(client: AsyncClient):
    """The internal install-status sink no longer exists — no route matches."""
    r = await client.post(
        "/api/internal/plugin-runner/plugins/acme-intel@main/install-status",
        json={"state": "building"},
        headers=_internal_h(RUNNER_SECRET),
    )
    assert r.status_code == 404, r.text
