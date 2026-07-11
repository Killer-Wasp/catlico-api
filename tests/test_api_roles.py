"""Role CRUD (/api/v1/roles).

Create/update/delete are superadmin-only; listing/reading a role is available to any
org member holding ``read:access`` (expanded ``read:role``). Permissions are the
domain-group grant vocabulary (e.g. ``read:investigation``), not fine-grained
capabilities.
"""
import pytest
from httpx import AsyncClient


def _auth(token, org=None):
    h = {"Authorization": f"Bearer {token}"}
    if org is not None:
        h["X-Organisation-Id"] = org
    return h


async def test_create_list_get_role(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "triage", "permissions": ["read:investigation", "write:investigation"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    role = r.json()
    assert role["name"] == "triage"
    assert set(role["permissions"]) == {"read:investigation", "write:investigation"}
    role_id = role["id"]

    listed = await client.get("/api/v1/roles/", headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == role_id for x in listed.json())

    got = await client.get(f"/api/v1/roles/{role_id}", headers=h)
    assert got.status_code == 200
    assert got.json()["name"] == "triage"


async def test_update_role_permissions(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "editable", "permissions": ["read:investigation"]},
        headers=h,
    )
    role_id = r.json()["id"]

    upd = await client.patch(
        f"/api/v1/roles/{role_id}",
        json={"permissions": ["read:investigation", "write:investigation", "read:intel"]},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert set(upd.json()["permissions"]) == {
        "read:investigation", "write:investigation", "read:intel",
    }


async def test_reject_unknown_permission(client: AsyncClient, admin_token, org_a):
    """The old fine-grained strings are no longer a valid grant vocabulary."""
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "legacy", "permissions": ["read:case"]},
        headers=_auth(admin_token, org_a.id),
    )
    assert r.status_code == 422, r.text


async def test_delete_role(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "temp", "permissions": ["read:investigation"]},
        headers=h,
    )
    role_id = r.json()["id"]

    d = await client.delete(f"/api/v1/roles/{role_id}", headers=h)
    assert d.status_code == 204
    assert (await client.get(f"/api/v1/roles/{role_id}", headers=h)).status_code == 404


async def test_duplicate_name_conflicts(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    body = {"name": "dup", "permissions": ["read:investigation"]}
    assert (await client.post("/api/v1/roles/", json=body, headers=h)).status_code == 201
    again = await client.post("/api/v1/roles/", json=body, headers=h)
    assert again.status_code == 409, again.text


async def test_get_missing_role_404(client: AsyncClient, admin_token, org_a):
    import uuid

    r = await client.get(
        f"/api/v1/roles/{uuid.uuid4()}", headers=_auth(admin_token, org_a.id)
    )
    assert r.status_code == 404


async def test_member_can_read_roles(
    client: AsyncClient, readonly_a_token, org_a, builtin_roles
):
    """A read-only member holds read:access, so can list/read roles (for the UI's
    role dropdowns), but cannot create them."""
    h = _auth(readonly_a_token, org_a.id)
    listed = await client.get("/api/v1/roles/", headers=h)
    assert listed.status_code == 200, listed.text

    denied = await client.post(
        "/api/v1/roles/",
        json={"name": "nope", "permissions": ["read:investigation"]},
        headers=h,
    )
    assert denied.status_code == 403, denied.text


async def test_non_member_cannot_read_roles(client: AsyncClient, viewer_token, org_a):
    """A non-superadmin who isn't a member of the org can't read its roles."""
    r = await client.get("/api/v1/roles/", headers=_auth(viewer_token, org_a.id))
    assert r.status_code == 403, r.text


async def test_unauthenticated_rejected(client: AsyncClient):
    r = await client.get("/api/v1/roles/")
    assert r.status_code in (401, 403)
