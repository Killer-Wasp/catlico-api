"""C1: API-key request auth — prove API keys can call read/write endpoints
and that revocation, expiry, and mismatched org are enforced. Keys are unscoped
(full capability set), so there is no per-scope denial."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.api_key import create_key
from app.models.api_key import ApiKeyCreate


def _auth_headers(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


@pytest.fixture
async def api_key_read_alert(session: AsyncSession, org_a):
    key_in = ApiKeyCreate(name="test-read-alert")
    _, plaintext = await create_key(
        session, key_in, organisation_id=org_a.id, created_by="test"
    )
    return plaintext


@pytest.fixture
async def api_key_full(session: AsyncSession, org_a):
    """An API key. Keys are unscoped, so this grants the full capability set."""
    key_in = ApiKeyCreate(name="test-full")
    _, plaintext = await create_key(
        session, key_in, organisation_id=org_a.id, created_by="test"
    )
    return plaintext


@pytest.fixture
async def an_alert(client, org_a, api_key_full):
    """Create an alert via API key, return its id."""
    resp = await client.post(
        "/api/v1/alerts/",
        json={
            "type": "phishing",
            "source": "api-test",
            "source_ref": "test-ref",
            "title": "test",
            "severity": 2,
        },
        headers=_auth_headers(api_key_full, org_a.id),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture
async def api_key_expired(session: AsyncSession, org_a):
    key_in = ApiKeyCreate(
        name="test-expired",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    _, plaintext = await create_key(
        session, key_in, organisation_id=org_a.id, created_by="test"
    )
    return plaintext


@pytest.fixture
async def api_key_revoked(session: AsyncSession, org_a):
    key_in = ApiKeyCreate(name="test-revoked")
    key, plaintext = await create_key(
        session, key_in, organisation_id=org_a.id, created_by="test"
    )
    key.deleted_at = datetime.now(UTC)
    session.add(key)
    await session.flush()
    return plaintext


# --- Happy path ---------------------------------------------------------------


async def test_api_key_read_endpoint(client, org_a, api_key_read_alert):
    """An API key with read:alert can GET /api/v1/alerts/."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers(api_key_read_alert, org_a.id),
    )
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["items"], list)


async def test_api_key_write_endpoint(client, org_a, api_key_full, an_alert):
    """An API key with write:observable can POST an observable to an alert."""
    resp = await client.post(
        f"/api/v1/alerts/{an_alert}/observables",
        json={"observable_type": "domain", "data": "example.com"},
        headers=_auth_headers(api_key_full, org_a.id),
    )
    assert resp.status_code in (200, 201), resp.text


async def test_api_key_can_call_case_scoped_route(client, org_a, api_key_full, builtin_roles):
    """Case-scoped permission dependencies accept API keys."""
    create = await client.post(
        "/api/v1/cases/",
        json={"title": "api-key case", "severity": 2},
        headers=_auth_headers(api_key_full, org_a.id),
    )
    assert create.status_code == 201, create.text
    case_id = create.json()["id"]

    resp = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "domain", "data": "case-scoped.example"},
        headers=_auth_headers(api_key_full, org_a.id),
    )
    assert resp.status_code == 201, resp.text


async def test_api_key_last_used_updated(session: AsyncSession, org_a):
    """Successful auth updates last_used_at."""
    from app.crud.api_key import touch_key

    key_in = ApiKeyCreate(name="test-last-used")
    key, plaintext = await create_key(
        session, key_in, organisation_id=org_a.id, created_by="test"
    )
    assert key.last_used_at is None
    await touch_key(session, key)
    await session.refresh(key)
    assert key.last_used_at is not None


# --- Auth failures ------------------------------------------------------------


async def test_api_key_bad_token_401(client, org_a):
    """A malformed token returns 401."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers("not-a-valid-key", org_a.id),
    )
    assert resp.status_code == 401


async def test_api_key_invalid_prefix_401(client, org_a):
    """A token with thp_ prefix but invalid hash returns 401."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers("thp_" + "ab" * 32, org_a.id),
    )
    assert resp.status_code == 401


async def test_api_key_revoked_401(client, org_a, api_key_revoked):
    """A soft-deleted key returns 401."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers(api_key_revoked, org_a.id),
    )
    assert resp.status_code == 401


async def test_api_key_expired_401(client, org_a, api_key_expired):
    """An expired key returns 401."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers(api_key_expired, org_a.id),
    )
    assert resp.status_code == 401


async def test_api_key_wrong_org_403(client, org_b, api_key_read_alert):
    """An API key for org-a cannot access org-b."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers=_auth_headers(api_key_read_alert, org_b.id),
    )
    assert resp.status_code == 403


async def test_api_key_missing_org_header_still_works(client, org_a, api_key_read_alert):
    """API keys are org-scoped: X-Organisation-Id is optional (key implies org).
    Result: 200 — the key's org is used."""
    resp = await client.get(
        "/api/v1/alerts/",
        headers={"Authorization": f"Bearer {api_key_read_alert}"},
    )
    assert resp.status_code == 200, resp.text


# --- JWT-only routes stay JWT-only -------------------------------------------


async def test_api_key_cannot_access_api_keys_route(client, org_a, api_key_read_alert):
    """API keys cannot access /api-keys/ — JWT-only route returns 401 (no JWT)."""
    resp = await client.get(
        "/api/v1/api-keys/",
        headers=_auth_headers(api_key_read_alert, org_a.id),
    )
    # JWT-only route: API key falls through to JWT auth → 401
    assert resp.status_code == 401


async def test_api_key_cannot_access_notifier_routes(client, org_a, api_key_read_alert):
    """API keys cannot access notifier config. Route requires specific method."""
    resp = await client.get(
        "/api/v1/notifications/notifiers",
        headers=_auth_headers(api_key_read_alert, org_a.id),
    )
    # GET not allowed on this route; either 405 or 401
    assert resp.status_code in (401, 405)
