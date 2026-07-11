import io
import uuid

import pytest
from httpx import AsyncClient


def _png_bytes() -> bytes:
    # Minimal 1x1 PNG.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c6360000002000154a24f5e0000000"
        "049454e44ae426082"
    )


async def test_get_me(client: AsyncClient, admin_user, admin_token):
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "admin@test.com"


async def test_get_me_unauthenticated(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


async def test_get_me_invalid_token(client: AsyncClient):
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer notavalidtoken"},
    )
    assert response.status_code == 401


async def test_update_me_email(client: AsyncClient, admin_user, admin_token):
    response = await client.patch(
        "/api/v1/users/me",
        json={"email": "newemail@test.com", "current_password": "password123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "newemail@test.com"


async def test_update_me_password(client: AsyncClient, admin_user, admin_token):
    response = await client.patch(
        "/api/v1/users/me",
        json={"new_password": "newpass456-long", "current_password": "password123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


async def test_update_me_requires_current_password(client: AsyncClient, admin_user, admin_token):
    response = await client.patch(
        "/api/v1/users/me",
        json={"email": "new@test.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


async def test_update_me_wrong_current_password(client: AsyncClient, admin_user, admin_token):
    response = await client.patch(
        "/api/v1/users/me",
        json={"email": "new@test.com", "current_password": "wrongpass"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 401


async def test_list_users_admin(client: AsyncClient, admin_user, admin_token):
    response = await client.get(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


async def test_list_users_non_superadmin_forbidden(client: AsyncClient, viewer_user, viewer_token):
    response = await client.get(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def test_create_user(client: AsyncClient, admin_user, admin_token):
    response = await client.post(
        "/api/v1/users/",
        json={
            "email": "new@test.com",
            "password": "pass123-longer",
            "first_name": "New",
            "last_name": "User",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "new@test.com"
    assert data["first_name"] == "New"
    assert data["last_name"] == "User"
    assert "id" in data
    assert "hashed_password" not in data


async def test_create_user_requires_names(client: AsyncClient, admin_user, admin_token):
    response = await client.post(
        "/api/v1/users/",
        json={"email": "noname@test.com", "password": "pass123-longer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


async def test_create_user_duplicate_email(client: AsyncClient, admin_user, admin_token):
    payload = {
        "email": "dup@test.com",
        "password": "pass123-longer",
        "first_name": "Dup",
        "last_name": "User",
    }
    await client.post(
        "/api/v1/users/",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response = await client.post(
        "/api/v1/users/",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409


async def test_create_user_non_superadmin_forbidden(client: AsyncClient, viewer_user, viewer_token):
    response = await client.post(
        "/api/v1/users/",
        json={
            "email": "x@test.com",
            "password": "pass-that-is-long",
            "first_name": "X",
            "last_name": "User",
        },
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


async def test_get_user_by_id(client: AsyncClient, admin_user, admin_token, viewer_user):
    response = await client.get(
        f"/api/v1/users/{viewer_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "viewer@test.com"


async def test_get_user_not_found(client: AsyncClient, admin_user, admin_token):
    response = await client.get(
        f"/api/v1/users/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


async def test_update_user_superadmin_flag(client: AsyncClient, admin_user, admin_token, viewer_user):
    response = await client.patch(
        f"/api/v1/users/{viewer_user.id}",
        json={"is_superadmin": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_superadmin"] is True


async def test_delete_user(client: AsyncClient, admin_user, admin_token, viewer_user):
    response = await client.delete(
        f"/api/v1/users/{viewer_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 204


async def test_delete_self_forbidden(client: AsyncClient, admin_user, admin_token):
    response = await client.delete(
        f"/api/v1/users/{admin_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400


async def test_delete_user_not_found(client: AsyncClient, admin_user, admin_token):
    response = await client.delete(
        f"/api/v1/users/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


# --- names ---


async def test_create_user_with_names(client: AsyncClient, admin_user, admin_token):
    response = await client.post(
        "/api/v1/users/",
        json={
            "email": "jane@test.com",
            "password": "pass123-longer",
            "first_name": "Jane",
            "last_name": "Doe",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["first_name"] == "Jane"
    assert data["last_name"] == "Doe"
    assert data["has_avatar"] is False


async def test_update_me_names_no_password_needed(
    client: AsyncClient, admin_user, admin_token
):
    response = await client.patch(
        "/api/v1/users/me",
        json={"first_name": "Ada", "last_name": "Lovelace"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["first_name"] == "Ada"
    assert data["last_name"] == "Lovelace"


# --- avatar ---


async def test_avatar_upload_get_delete(client: AsyncClient, admin_user, admin_token):
    auth = {"Authorization": f"Bearer {admin_token}"}
    up = await client.put(
        "/api/v1/users/me/avatar",
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
        headers=auth,
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_avatar"] is True

    got = await client.get(f"/api/v1/users/{admin_user.id}/avatar", headers=auth)
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("image/png")
    assert got.content == _png_bytes()

    deleted = await client.delete("/api/v1/users/me/avatar", headers=auth)
    assert deleted.status_code == 200
    assert deleted.json()["has_avatar"] is False

    gone = await client.get(f"/api/v1/users/{admin_user.id}/avatar", headers=auth)
    assert gone.status_code == 404


async def test_avatar_rejects_non_image(client: AsyncClient, admin_user, admin_token):
    response = await client.put(
        "/api/v1/users/me/avatar",
        files={"file": ("a.txt", io.BytesIO(b"hello"), "text/plain")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


# --- search ---


async def test_search_users_by_email(
    client: AsyncClient, org_a, analyst_a, analyst_b, analyst_a_token
):
    response = await client.get(
        "/api/v1/users/search",
        params={"q": "analyst"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()]
    assert analyst_a.email in emails
    assert analyst_b.email not in emails


async def test_search_users_by_name(
    client: AsyncClient, session, org_a, builtin_roles, admin_user, analyst_a_token
):
    from app.crud.organisation_member import add_member
    from app.crud.user import create_user
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.user import UserCreate

    user = await create_user(
        session,
        UserCreate(email="grace@test.com", first_name="Grace", last_name="Hopper"),
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["analyst"].id),
        created_by=str(admin_user.id),
    )
    # full-name query spanning both fields
    response = await client.get(
        "/api/v1/users/search",
        params={"q": "grace hopper"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    emails = [u["email"] for u in response.json()]
    assert "grace@test.com" in emails


async def test_search_users_requires_active_org(
    client: AsyncClient, analyst_a_token
):
    response = await client.get(
        "/api/v1/users/search",
        params={"q": "analyst"},
        headers={"Authorization": f"Bearer {analyst_a_token}"},
    )
    assert response.status_code == 400


async def test_search_users_available_to_non_admin(
    client: AsyncClient, org_a, analyst_a, analyst_a_token
):
    response = await client.get(
        "/api/v1/users/search",
        params={"q": "analyst-a"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    assert [u["email"] for u in response.json()] == [analyst_a.email]
