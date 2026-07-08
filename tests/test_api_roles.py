"""Superadmin-only role CRUD (/api/v1/roles)."""
import pytest
from httpx import AsyncClient


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_list_get_role(client: AsyncClient, admin_token):
    h = _auth(admin_token)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "triage", "permissions": ["read:case", "write:case"]},
        headers=h,
    )
    assert r.status_code == 201, r.text
    role = r.json()
    assert role["name"] == "triage"
    assert set(role["permissions"]) == {"read:case", "write:case"}
    role_id = role["id"]

    listed = await client.get("/api/v1/roles/", headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == role_id for x in listed.json())

    got = await client.get(f"/api/v1/roles/{role_id}", headers=h)
    assert got.status_code == 200
    assert got.json()["name"] == "triage"


async def test_update_role_permissions(client: AsyncClient, admin_token):
    h = _auth(admin_token)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "editable", "permissions": ["read:case"]},
        headers=h,
    )
    role_id = r.json()["id"]

    upd = await client.patch(
        f"/api/v1/roles/{role_id}",
        json={"permissions": ["read:case", "write:case", "read:task"]},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert set(upd.json()["permissions"]) == {"read:case", "write:case", "read:task"}


async def test_delete_role(client: AsyncClient, admin_token):
    h = _auth(admin_token)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "temp", "permissions": ["read:case"]},
        headers=h,
    )
    role_id = r.json()["id"]

    d = await client.delete(f"/api/v1/roles/{role_id}", headers=h)
    assert d.status_code == 204
    assert (await client.get(f"/api/v1/roles/{role_id}", headers=h)).status_code == 404


async def test_duplicate_name_conflicts(client: AsyncClient, admin_token):
    h = _auth(admin_token)
    body = {"name": "dup", "permissions": ["read:case"]}
    assert (await client.post("/api/v1/roles/", json=body, headers=h)).status_code == 201
    again = await client.post("/api/v1/roles/", json=body, headers=h)
    assert again.status_code == 409, again.text


async def test_get_missing_role_404(client: AsyncClient, admin_token):
    import uuid

    r = await client.get(f"/api/v1/roles/{uuid.uuid4()}", headers=_auth(admin_token))
    assert r.status_code == 404


@pytest.mark.parametrize("method,path", [("get", "/"), ("post", "/")])
async def test_non_superadmin_forbidden(
    client: AsyncClient, viewer_token, method, path
):
    """A regular (non-superadmin) user cannot touch roles."""
    h = _auth(viewer_token)
    if method == "get":
        r = await client.get(f"/api/v1/roles{path}", headers=h)
    else:
        r = await client.post(
            f"/api/v1/roles{path}",
            json={"name": "x", "permissions": ["read:case"]},
            headers=h,
        )
    assert r.status_code == 403, r.text


async def test_unauthenticated_rejected(client: AsyncClient):
    r = await client.get("/api/v1/roles/")
    assert r.status_code in (401, 403)
