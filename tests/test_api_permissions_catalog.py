"""Permission catalog endpoint, effective-permission endpoint, and the
subset-of-caller enforcement on API-key scopes."""
import pytest
from httpx import AsyncClient

from app.core.security import TokenPayload, create_access_token
from app.crud.organisation_member import add_member
from app.crud.role import create_role
from app.crud.user import create_user
from app.models.organisation_member import OrganisationMemberCreate
from app.models.role import RoleCreate
from app.models.user import UserCreate


def _h(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org}


async def test_permission_catalog_lists_ten_groups(client: AsyncClient, admin_token):
    r = await client.get("/api/v1/permissions/", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    catalog = r.json()
    keys = {c["key"] for c in catalog}
    assert keys == {
        "read:investigation", "write:investigation",
        "read:intel", "write:intel",
        "run:enrichment", "run:function",
        "read:org", "write:org",
        "read:access", "write:access",
    }
    assert all(c["kind"] in {"read", "write", "run"} for c in catalog)


async def test_me_permissions_expands_groups(
    client: AsyncClient, readonly_a_token, org_a, builtin_roles
):
    """A read-only member's stored group grants expand to fine-grained capabilities."""
    r = await client.get(
        "/api/v1/users/me/permissions", headers=_h(readonly_a_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_superadmin"] is False
    # read:investigation expands to read:case; write is not granted.
    assert "read:case" in body["permissions"]
    assert "write:case" not in body["permissions"]
    # raw groups are the coarse grant vocabulary
    assert "read:investigation" in body["groups"]
    assert "write:investigation" not in body["groups"]


async def test_api_key_scopes_must_be_known_groups(
    client: AsyncClient, analyst_a_token, org_a
):
    """analyst_a is seeded org-admin (all groups); a fine-grained/unknown scope
    string is not a valid grant vocabulary value."""
    r = await client.post(
        "/api/v1/api-keys/",
        json={"name": "k", "scopes": ["read:case"]},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 422, r.text


async def test_api_key_scopes_bounded_by_caller(
    client: AsyncClient, session, org_a, builtin_roles, admin_user
):
    """A member whose role can manage the org (write:org) but lacks write:investigation
    cannot mint a key carrying write:investigation, but can mint one with write:org."""
    role = await create_role(
        session,
        RoleCreate(name="org-manager", permissions=["write:org", "read:access"]),
        organisation_id=org_a.id,
        created_by=str(admin_user.id),
    )
    user = await create_user(
        session,
        UserCreate(first_name="Org", last_name="Manager", email="orgmgr@test.com", password="password123"),
    )
    await add_member(
        session, org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=role.id),
        created_by=str(admin_user.id),
    )
    token = create_access_token(
        TokenPayload(user_id=user.id, is_superadmin=False, organisations=[org_a.id])
    )

    denied = await client.post(
        "/api/v1/api-keys/",
        json={"name": "escalate", "scopes": ["write:investigation"]},
        headers=_h(token, org_a.id),
    )
    assert denied.status_code == 403, denied.text

    ok = await client.post(
        "/api/v1/api-keys/",
        json={"name": "legit", "scopes": ["write:org"]},
        headers=_h(token, org_a.id),
    )
    assert ok.status_code == 201, ok.text
    assert set(ok.json()["scopes"]) == {"write:org"}
