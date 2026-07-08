"""Superadmin-managed observable-type reference data (/api/v1/observable-types).

Note: the observable_type table is intentionally NOT truncated between tests
(it holds seeded reference data), so every test here cleans up anything it
creates to avoid leaking rows into sibling tests.
"""
import pytest
from httpx import AsyncClient


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_list_visible_to_regular_user(
    client: AsyncClient, viewer_token, observable_types
):
    r = await client.get("/api/v1/observable-types/", headers=_auth(viewer_token))
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    # Built-in reference data is seeded and readable by any authenticated user.
    assert set(observable_types).issubset(names)


async def test_create_and_delete_type(client: AsyncClient, admin_token):
    h = _auth(admin_token)
    name = "test-custom-type"
    try:
        c = await client.post(
            "/api/v1/observable-types/",
            json={"name": name, "is_attachment": False},
            headers=h,
        )
        assert c.status_code == 201, c.text
        assert c.json()["name"] == name

        names = {
            t["name"]
            for t in (await client.get("/api/v1/observable-types/", headers=h)).json()
        }
        assert name in names
    finally:
        d = await client.delete(f"/api/v1/observable-types/{name}", headers=h)
        assert d.status_code in (204, 404)

    names = {
        t["name"]
        for t in (await client.get("/api/v1/observable-types/", headers=h)).json()
    }
    assert name not in names


async def test_duplicate_type_conflicts(
    client: AsyncClient, admin_token, observable_types
):
    # Re-creating a seeded type must 409 (and not insert a duplicate).
    existing = next(iter(observable_types))
    r = await client.post(
        "/api/v1/observable-types/",
        json={"name": existing, "is_attachment": False},
        headers=_auth(admin_token),
    )
    assert r.status_code == 409, r.text


async def test_delete_missing_type_404(client: AsyncClient, admin_token):
    r = await client.delete(
        "/api/v1/observable-types/does-not-exist", headers=_auth(admin_token)
    )
    assert r.status_code == 404


async def test_non_superadmin_cannot_mutate(client: AsyncClient, viewer_token):
    h = _auth(viewer_token)
    c = await client.post(
        "/api/v1/observable-types/",
        json={"name": "sneaky", "is_attachment": False},
        headers=h,
    )
    assert c.status_code == 403, c.text
    d = await client.delete("/api/v1/observable-types/ip", headers=h)
    assert d.status_code == 403
