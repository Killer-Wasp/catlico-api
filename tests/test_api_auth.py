import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.security import decode_refresh_token
from app.crud.user import create_user, update_user
from app.models.auth import RefreshToken
from app.models.user import UserCreate, UserUpdate


async def test_login_success(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    # Clean break: the refresh token never appears in the body.
    assert "refresh_token" not in data


async def test_login_sets_refresh_cookie(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"].lower()
    assert set_cookie.startswith("catlico_refresh=")
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/api/v1/auth" in set_cookie
    assert "secure" in set_cookie
    assert "max-age=" in set_cookie


async def test_login_cookie_is_signed_refresh_jwt(client: AsyncClient, admin_user):
    await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    token = client.cookies.get("catlico_refresh")
    assert token is not None
    assert token.count(".") == 2
    assert decode_refresh_token(token) is not None


async def test_login_persists_refresh_token(client: AsyncClient, session, admin_user):
    await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    decoded = decode_refresh_token(client.cookies.get("catlico_refresh"))
    assert decoded is not None
    token, _ = decoded
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token == token))
    ).scalar_one_or_none()
    assert row is not None


async def test_refresh_returns_new_access_token(client: AsyncClient, admin_user):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_refresh_rejects_revoked_token(client: AsyncClient, session, admin_user):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    decoded = decode_refresh_token(login.json()["refresh_token"])
    assert decoded is not None
    token, _ = decoded
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token == token))
    ).scalar_one_or_none()
    assert row is not None
    await session.delete(row)
    await session.commit()

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login.json()["refresh_token"]},
    )
    assert response.status_code == 401


async def test_refresh_rejects_tampered_token(client: AsyncClient, admin_user):
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    refresh_token = login.json()["refresh_token"]
    header, payload, signature = refresh_token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ".".join((header, payload, replacement + signature[1:]))
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tampered},
    )
    assert response.status_code == 401


async def test_refresh_rejects_invalid_token(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "not-a-jwt"},
    )
    assert response.status_code == 401


async def test_refresh_rejects_access_token(client: AsyncClient, admin_user):
    """An access token must not be usable where a refresh token is expected."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    access_token = login.json()["access_token"]

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert response.status_code == 401


async def test_login_wrong_password(client: AsyncClient, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "wrong"},
    )
    assert response.status_code == 401


async def test_login_unknown_user(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "secret"},
    )
    assert response.status_code == 401


async def test_login_inactive_user(client: AsyncClient, session, admin_user):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="inactive@test.com", password="pass123"))
    await update_user(session, user, UserUpdate(is_active=False))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@test.com", "password": "pass123"},
    )
    assert response.status_code == 403


async def test_login_no_password_user(client: AsyncClient, session):
    await create_user(session, UserCreate(first_name="Test", last_name="User", email="oauth@test.com"))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "oauth@test.com", "password": "anything"},
    )
    assert response.status_code == 401
