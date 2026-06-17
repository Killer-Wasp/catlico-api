import uuid

import pytest
from httpx import AsyncClient


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
        json={"new_password": "newpass456", "current_password": "password123"},
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
        json={"email": "new@test.com", "password": "pass123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "new@test.com"
    assert "id" in data
    assert "hashed_password" not in data


async def test_create_user_duplicate_email(client: AsyncClient, admin_user, admin_token):
    payload = {"email": "dup@test.com", "password": "pass123"}
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
        json={"email": "x@test.com", "password": "pass"},
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
