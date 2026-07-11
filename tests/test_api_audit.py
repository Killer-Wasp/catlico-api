"""Superadmin audit-log listing + filters (/api/v1/audit)."""
import pytest
from httpx import AsyncClient


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _make_role(client, token, name, org_id):
    r = await client.post(
        "/api/v1/roles/",
        json={"name": name, "permissions": ["read:investigation"]},
        headers={**_auth(token), "X-Organisation-Id": org_id},
    )
    assert r.status_code == 201, r.text


async def test_list_audit_after_action(client: AsyncClient, admin_token, org_a):
    # Creating a role writes an audit row (action="create", object_type="role").
    await _make_role(client, admin_token, "audited", org_a.id)

    r = await client.get("/api/v1/audit/", headers=_auth(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    assert any(
        row["action"] == "create" and row["object_type"] == "role"
        for row in body["items"]
    )


async def test_filter_by_object_type(client: AsyncClient, admin_token, org_a):
    await _make_role(client, admin_token, "filtered", org_a.id)

    r = await client.get(
        "/api/v1/audit/",
        params={"object_type": "role", "action": "create"},
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(row["object_type"] == "role" for row in body["items"])

    # A filter that matches nothing yields an empty page.
    none = await client.get(
        "/api/v1/audit/", params={"object_type": "nonexistent"}, headers=_auth(admin_token)
    )
    assert none.status_code == 200
    assert none.json()["total"] == 0


async def test_audit_requires_superadmin(client: AsyncClient, viewer_token):
    r = await client.get("/api/v1/audit/", headers=_auth(viewer_token))
    assert r.status_code == 403, r.text
