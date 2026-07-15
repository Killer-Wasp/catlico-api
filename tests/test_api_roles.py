"""Role CRUD (/api/v1/roles).

Create/update/delete are superadmin-only; listing/reading a role is available to any
org member holding ``read:role`` (granted via ``manage:users``). Permissions are the
flat per-entity grant vocabulary (e.g. ``read:case``, ``manage:org``), not the old
domain groups.
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


async def test_update_role_permissions(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "editable", "permissions": ["read:case"]},
        headers=h,
    )
    role_id = r.json()["id"]

    upd = await client.patch(
        f"/api/v1/roles/{role_id}",
        json={"permissions": ["read:case", "write:case", "manage:org"]},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert set(upd.json()["permissions"]) == {
        "read:case", "write:case", "manage:org",
    }


async def test_reject_unknown_permission(client: AsyncClient, admin_token, org_a):
    """The old domain-group strings are no longer a valid grant vocabulary."""
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "legacy", "permissions": ["read:investigation"]},
        headers=_auth(admin_token, org_a.id),
    )
    assert r.status_code == 422, r.text


async def test_delete_role(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/roles/",
        json={"name": "temp", "permissions": ["read:case"]},
        headers=h,
    )
    role_id = r.json()["id"]

    d = await client.delete(f"/api/v1/roles/{role_id}", headers=h)
    assert d.status_code == 204
    assert (await client.get(f"/api/v1/roles/{role_id}", headers=h)).status_code == 404


async def test_duplicate_name_conflicts(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    body = {"name": "dup", "permissions": ["read:case"]}
    assert (await client.post("/api/v1/roles/", json=body, headers=h)).status_code == 201
    again = await client.post("/api/v1/roles/", json=body, headers=h)
    assert again.status_code == 409, again.text


async def test_get_missing_role_404(client: AsyncClient, admin_token, org_a):
    import uuid

    r = await client.get(
        f"/api/v1/roles/{uuid.uuid4()}", headers=_auth(admin_token, org_a.id)
    )
    assert r.status_code == 404


async def test_readonly_member_cannot_read_or_write_roles(
    client: AsyncClient, readonly_a_token, org_a, builtin_roles
):
    """Reading roles now requires read:role, which is granted only via manage:users
    (Finding 3): the flat read-only builtin no longer carries it, so a read-only
    member is denied both listing and creating roles."""
    h = _auth(readonly_a_token, org_a.id)
    listed = await client.get("/api/v1/roles/", headers=h)
    assert listed.status_code == 403, listed.text

    denied = await client.post(
        "/api/v1/roles/",
        json={"name": "nope", "permissions": ["read:case"]},
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


# --- Built-in role protection ---


async def test_builtin_roles_flagged_custom_not(client: AsyncClient, admin_token, org_a):
    """Seeded built-in roles expose is_builtin=True; custom roles are False."""
    h = _auth(admin_token, org_a.id)
    listed = await client.get("/api/v1/roles/", headers=h)
    assert listed.status_code == 200, listed.text
    by_name = {r["name"]: r for r in listed.json()}
    for name in ("org-admin", "analyst", "read-only"):
        assert by_name[name]["is_builtin"] is True, name

    created = await client.post(
        "/api/v1/roles/",
        json={"name": "custom", "permissions": ["read:case"]},
        headers=h,
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_builtin"] is False


async def test_cannot_delete_builtin_role(client: AsyncClient, admin_token, org_a):
    h = _auth(admin_token, org_a.id)
    listed = await client.get("/api/v1/roles/", headers=h)
    admin_role = next(r for r in listed.json() if r["name"] == "org-admin")
    d = await client.delete(f"/api/v1/roles/{admin_role['id']}", headers=h)
    assert d.status_code == 409, d.text
    assert "built-in" in d.json()["detail"].lower()
    # still present after the rejected delete
    still = await client.get(f"/api/v1/roles/{admin_role['id']}", headers=h)
    assert still.status_code == 200


async def test_cannot_patch_builtin_role_permissions(
    client: AsyncClient, admin_token, org_a
):
    h = _auth(admin_token, org_a.id)
    listed = await client.get("/api/v1/roles/", headers=h)
    admin_role = next(r for r in listed.json() if r["name"] == "org-admin")
    upd = await client.patch(
        f"/api/v1/roles/{admin_role['id']}",
        json={"permissions": ["read:case"]},
        headers=h,
    )
    assert upd.status_code == 409, upd.text
    assert "built-in" in upd.json()["detail"].lower()
    # permissions unchanged (org-admin still holds the full grant surface)
    unchanged = await client.get(f"/api/v1/roles/{admin_role['id']}", headers=h)
    assert len(unchanged.json()["permissions"]) > 1


async def test_seed_stamps_already_seeded_org(session, org_a):
    """Orgs seeded before is_builtin existed get re-stamped idempotently on re-seed
    (mirrors the startup upsert running against already-seeded data)."""
    from sqlalchemy import update

    from app.crud.role import seed_org_builtin_roles
    from app.models.role import Role

    # Simulate legacy rows written before the flag existed.
    await session.execute(
        update(Role).where(Role.organisation_id == org_a.id).values(is_builtin=False)
    )
    await session.commit()

    roles = await seed_org_builtin_roles(session, org_a.id)
    for name in ("org-admin", "analyst", "read-only"):
        await session.refresh(roles[name])
        assert roles[name].is_builtin is True, name
