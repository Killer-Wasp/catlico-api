"""Tests for the enterprise extension seam (plans/phase-3 §3.0).

Two halves:
  * OSS / no-extension — providers empty, capabilities all-false, login unchanged.
  * Fixture-extension — each hook (router, provider, capability, second-factor)
    exercised end-to-end through the live app.
"""

import pytest
from httpx import AsyncClient


@pytest.fixture
def installed_extension():
    """Register the fixture extension in the live registry and mount its routers
    on the app, then tear both back down so no state leaks into other tests."""
    from app.core.extensions import install_extension, registry
    from app.main import app
    from tests.fixtures.extension import fixture_extension

    saved_exts = list(registry.extensions)
    saved_routes = list(app.router.routes)
    install_extension(app, fixture_extension)
    try:
        yield fixture_extension
    finally:
        registry.reset(saved_exts)
        app.router.routes[:] = saved_routes


# --- OSS / no extension installed --------------------------------------------


async def test_auth_providers_empty_in_oss(client: AsyncClient):
    """With no extension installed the login page gets an empty provider list."""
    response = await client.get("/api/v1/auth/providers")
    assert response.status_code == 200
    assert response.json() == []


async def test_system_capabilities_all_false_in_oss(client: AsyncClient):
    response = await client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    assert response.json() == {"sso": False, "mfa": False}


async def test_login_unchanged_without_extension(client: AsyncClient, admin_user):
    """The login response is exactly today's Token shape — no mfa_required."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"access_token", "token_type"}
    assert data["token_type"] == "bearer"
    assert "mfa_required" not in data


# --- Fixture extension installed ---------------------------------------------


async def test_fixture_router_is_mounted(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/fixture-ext/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True}


async def test_fixture_provider_advertised(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/auth/providers")
    assert response.status_code == 200
    providers = response.json()
    assert providers == [
        {
            "id": "fixture-oidc",
            "name": "Fixture SSO",
            "kind": "oidc",
            "authorize_path": "/api/v1/fixture-ext/authorize",
        }
    ]


async def test_fixture_capabilities_merged(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    assert response.json() == {"sso": True, "mfa": True}


async def test_login_calls_second_factor_hook(
    client: AsyncClient, admin_user, installed_extension
):
    """When an extension's second_factor_hook returns a challenge, login responds
    with the mfa_required envelope instead of issuing tokens."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mfa_required"] is True
    assert data["pending_token"] == f"pending-{admin_user.id}"
    assert "access_token" not in data
    # No refresh cookie is set for a challenge (tokens were never issued).
    assert "set-cookie" not in {k.lower() for k in response.headers}
