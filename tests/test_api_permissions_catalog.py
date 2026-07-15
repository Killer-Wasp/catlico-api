"""Permission catalog endpoint and effective-permission endpoint.

RBAC is now a flat per-entity grant vocabulary (read/write/delete per
case/task/alert/observable + manage:users/manage:org). API keys are unscoped, so
the old subset-of-caller scope enforcement is gone.
"""
from httpx import AsyncClient


def _h(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org}


async def test_permission_catalog_lists_all_groups(client: AsyncClient, admin_token):
    r = await client.get("/api/v1/permissions/", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    catalog = r.json()
    keys = {c["key"] for c in catalog}
    assert keys == {
        "read:case", "write:case", "delete:case",
        "read:task", "write:task", "delete:task",
        "read:alert", "write:alert", "delete:alert",
        "read:observable", "write:observable", "delete:observable",
        "manage:users", "manage:org",
    }
    assert all(c["kind"] in {"read", "write", "delete", "manage"} for c in catalog)


async def test_me_permissions_expands_groups(
    client: AsyncClient, readonly_a_token, org_a, builtin_roles
):
    """A read-only member's stored grants. In the flat vocabulary each per-entity
    read group is its own capability, so read:case is present while write:case is
    not. (The read-only builtin now grants only the four entity read:* — it no
    longer carries read:knowledge_base / read:organisation, which live in
    manage:org — see Finding 3.)"""
    r = await client.get(
        "/api/v1/users/me/permissions", headers=_h(readonly_a_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_superadmin"] is False
    assert "read:case" in body["permissions"]
    assert "write:case" not in body["permissions"]
    # raw groups are the flat grant vocabulary
    assert "read:case" in body["groups"]
    assert "write:case" not in body["groups"]
