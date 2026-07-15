from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import select

from app.core.security import decode_refresh_token
from app.crud.user import create_user, get_user_by_email, update_user
from app.models.audit import Audit
from app.models.auth import RefreshToken
from app.models.user import User, UserCreate, UserUpdate

CSRF_HEADERS = {"X-Requested-With": "XMLHttpRequest"}


async def _login(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200


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


async def test_login_sets_refresh_cookie(client: AsyncClient, admin_user, monkeypatch):
    # The `secure` flag is gated on COOKIE_SECURE, which local .env sets to false
    # for http dev. Pin it true so this test verifies the secure-by-default
    # behaviour regardless of the developer's ambient override.
    from app.core.configs import settings

    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
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
    await _login(client)
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_refresh_rotates_refresh_token(client: AsyncClient, session, admin_user):
    """Every refresh consumes the presented token and issues a replacement: the
    old cookie is single-use (a replay 401s) and the new one keeps working."""
    await _login(client)
    old_cookie = client.cookies.get("catlico_refresh")

    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 200
    new_cookie = client.cookies.get("catlico_refresh")
    assert new_cookie is not None
    assert new_cookie != old_cookie

    # The consumed token's row is gone from the DB.
    old_token, _ = decode_refresh_token(old_cookie)
    row = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token == old_token)
        )
    ).scalar_one_or_none()
    assert row is None

    # Replaying the old cookie fails; the rotated one succeeds.
    client.cookies.set(
        "catlico_refresh", old_cookie, domain="test.local", path="/api/v1/auth"
    )
    replay = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert replay.status_code == 401

    client.cookies.set(
        "catlico_refresh", new_cookie, domain="test.local", path="/api/v1/auth"
    )
    ok = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert ok.status_code == 200


async def test_password_change_revokes_all_sessions(
    client: AsyncClient, session, admin_user
):
    """Changing the password via PATCH /users/me logs out every session: all
    refresh tokens are deleted and the pre-change cookie can no longer refresh."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]
    # A second session that should also be revoked.
    await _login(client)

    r = await client.patch(
        "/api/v1/users/me",
        json={"current_password": "password123", "new_password": "a-new-password-123"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200, r.text

    rows = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        )
    ).scalars().all()
    assert rows == []

    refresh = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert refresh.status_code == 401


async def test_refresh_rejects_revoked_token(client: AsyncClient, session, admin_user):
    await _login(client)
    decoded = decode_refresh_token(client.cookies.get("catlico_refresh"))
    assert decoded is not None
    token, _ = decoded
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token == token))
    ).scalar_one_or_none()
    assert row is not None
    await session.delete(row)
    await session.commit()

    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_refresh_rejects_tampered_token(client: AsyncClient, admin_user):
    await _login(client)
    refresh_token = client.cookies.get("catlico_refresh")
    header, payload, signature = refresh_token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = ".".join((header, payload, replacement + signature[1:]))
    # httpx's stdlib cookiejar stores the dotless test host "test" as domain
    # "test.local"; matching it makes this cookie REPLACE the login one instead
    # of coexisting (which would silently send the valid cookie and fake a pass).
    client.cookies.set("catlico_refresh", tampered, domain="test.local", path="/api/v1/auth")
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_refresh_rejects_invalid_token(client: AsyncClient):
    client.cookies.set("catlico_refresh", "not-a-jwt", domain="test.local", path="/api/v1/auth")
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_refresh_rejects_access_token(client: AsyncClient, admin_user):
    """An access token must not be usable where a refresh token is expected."""
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    client.cookies.set(
        "catlico_refresh", login.json()["access_token"], domain="test.local", path="/api/v1/auth"
    )
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_refresh_requires_cookie(client: AsyncClient):
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_refresh_ignores_body_token(client: AsyncClient, admin_user):
    """Clean break: the old body-based contract must not work."""
    await _login(client)
    token = client.cookies.get("catlico_refresh")
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/refresh", headers=CSRF_HEADERS, json={"refresh_token": token}
    )
    assert response.status_code == 401


async def test_refresh_requires_csrf_header(client: AsyncClient, admin_user):
    await _login(client)
    response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 403


async def test_refresh_rejects_unknown_origin(client: AsyncClient, admin_user):
    await _login(client)
    response = await client.post(
        "/api/v1/auth/refresh",
        headers={**CSRF_HEADERS, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


async def test_refresh_allows_allowlisted_origin(client: AsyncClient, admin_user):
    from app.core.configs import settings

    await _login(client)
    response = await client.post(
        "/api/v1/auth/refresh",
        headers={**CSRF_HEADERS, "Origin": settings.FRONTEND_HOST},
    )
    assert response.status_code == 200


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
    from app.crud.user import set_password

    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="inactive@test.com"))
    await set_password(session, user, "pass123-long-enough")
    await update_user(session, user, UserUpdate(is_active=False))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@test.com", "password": "pass123-long-enough"},
    )
    assert response.status_code == 403


async def test_login_no_password_user(client: AsyncClient, session):
    await create_user(session, UserCreate(first_name="Test", last_name="User", email="oauth@test.com"))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "oauth@test.com", "password": "anything"},
    )
    assert response.status_code == 401


async def test_login_flagged_user_gets_reset_required_not_a_session(
    client: AsyncClient, session, admin_user
):
    """F.3 force-reset gate: a must_change_password user who proves their password
    gets NO session — only `password_reset_required` + a single-use reset token, so
    the client can bounce straight to the reset page."""
    admin_user.must_change_password = True
    session.add(admin_user)
    await session.flush()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["password_reset_required"] is True
    assert body.get("reset_token")
    # No session was issued.
    assert "access_token" not in body
    assert "catlico_refresh" not in response.cookies


async def test_login_flagged_user_reset_token_completes_reset_and_unblocks(
    client: AsyncClient, session, admin_user
):
    """The reset token returned by the gate drives the ordinary reset endpoint;
    once used, the flag is cleared and a normal login succeeds."""
    admin_user.must_change_password = True
    session.add(admin_user)
    await session.flush()

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    reset_token = blocked.json()["reset_token"]

    done = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": reset_token, "new_password": "a-brand-new-password"},
    )
    assert done.status_code == 200

    ok = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "a-brand-new-password"},
    )
    assert ok.status_code == 200
    assert ok.json().get("access_token")
    await session.refresh(admin_user)
    assert admin_user.must_change_password is False


async def test_login_flagged_gate_is_after_lockout_no_oracle(
    client: AsyncClient, session, admin_user
):
    """The gate sits AFTER the is_active/lockout checks: a locked flagged user still
    gets the generic 401 (no password_reset_required leak — no enumeration oracle)."""
    admin_user.must_change_password = True
    admin_user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    session.add(admin_user)
    await session.flush()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 401
    assert "password_reset_required" not in response.text


async def test_logout_revokes_session_and_clears_cookie(
    client: AsyncClient, session, admin_user
):
    await _login(client)
    decoded = decode_refresh_token(client.cookies.get("catlico_refresh"))
    assert decoded is not None
    token, _ = decoded

    response = await client.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    assert response.status_code == 204
    set_cookie = response.headers["set-cookie"].lower()
    assert "catlico_refresh=" in set_cookie
    assert "max-age=0" in set_cookie or "expires=" in set_cookie

    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token == token))
    ).scalar_one_or_none()
    assert row is None

    # The clearing Set-Cookie empties the client jar: refresh must now fail.
    response = await client.post("/api/v1/auth/refresh", headers=CSRF_HEADERS)
    assert response.status_code == 401


async def test_logout_without_cookie_is_idempotent(client: AsyncClient):
    response = await client.post("/api/v1/auth/logout", headers=CSRF_HEADERS)
    assert response.status_code == 204


async def test_logout_requires_csrf_header(client: AsyncClient, admin_user):
    await _login(client)
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 403


# --- Session device info + current-session marker (§1.5a) ---


async def _login_with(client: AsyncClient, *, user_agent=None, xff=None) -> str:
    """Log in with optional device headers; returns the access token."""
    headers = {}
    if user_agent is not None:
        headers["User-Agent"] = user_agent
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def _sessions(client: AsyncClient, access_token: str):
    response = await client.get(
        "/api/v1/auth/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_login_stamps_user_agent_and_leftmost_ip(
    client: AsyncClient, session, admin_user
):
    """Login records the UA header and the leftmost X-Forwarded-For hop (the
    original client) on the refresh-token row, and the list endpoint surfaces
    both."""
    access_token = await _login_with(
        client,
        user_agent="Mozilla/5.0 (TestBrowser)",
        xff="203.0.113.5, 70.41.3.18, 150.172.238.178",
    )
    rows = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_agent == "Mozilla/5.0 (TestBrowser)"
    assert rows[0].ip_address == "203.0.113.5"

    data = await _sessions(client, access_token)
    assert len(data) == 1
    assert data[0]["user_agent"] == "Mozilla/5.0 (TestBrowser)"
    assert data[0]["ip_address"] == "203.0.113.5"


async def test_login_ip_falls_back_to_client_host(
    client: AsyncClient, session, admin_user
):
    """With no forwarded header, the direct socket peer is stored."""
    await _login_with(client, user_agent="Browser-A")
    rows = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].ip_address == "127.0.0.1"


async def test_login_truncates_long_user_agent(
    client: AsyncClient, session, admin_user
):
    await _login_with(client, user_agent="U" * 1000)
    rows = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        )
    ).scalars().all()
    assert rows[0].user_agent == "U" * 400


async def test_sessions_marks_caller_current(client: AsyncClient, admin_user):
    """The session behind the caller's refresh cookie is flagged is_current."""
    access_token = await _login_with(client, user_agent="Browser-A")
    data = await _sessions(client, access_token)
    assert len(data) == 1
    assert data[0]["is_current"] is True


async def test_sessions_two_logins_flag_only_current(client: AsyncClient, admin_user):
    """Two logins from different UAs yield two sessions; only the one matching
    the current refresh cookie (the most recent login) is current."""
    await _login_with(client, user_agent="Browser-A", xff="1.1.1.1")
    access_token = await _login_with(client, user_agent="Browser-B", xff="2.2.2.2")

    data = await _sessions(client, access_token)
    assert len(data) == 2
    current = [s for s in data if s["is_current"]]
    others = [s for s in data if not s["is_current"]]
    assert len(current) == 1
    assert len(others) == 1
    assert current[0]["user_agent"] == "Browser-B"
    assert current[0]["ip_address"] == "2.2.2.2"
    assert others[0]["user_agent"] == "Browser-A"


async def test_sessions_none_current_without_cookie(client: AsyncClient, admin_user):
    """Absent refresh cookie -> nothing is current."""
    access_token = await _login_with(client, user_agent="Browser-A")
    client.cookies.clear()
    data = await _sessions(client, access_token)
    assert len(data) == 1
    assert data[0]["is_current"] is False


async def test_refresh_restamps_device_info(client: AsyncClient, session, admin_user):
    """Rotation on refresh re-stamps the replacement row with the current
    request's UA/IP."""
    await _login_with(client, user_agent="Browser-A", xff="1.1.1.1")
    response = await client.post(
        "/api/v1/auth/refresh",
        headers={**CSRF_HEADERS, "User-Agent": "Browser-B", "X-Forwarded-For": "9.9.9.9"},
    )
    assert response.status_code == 200
    rows = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == admin_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_agent == "Browser-B"
    assert rows[0].ip_address == "9.9.9.9"


async def test_sessions_isolated_per_user(
    client: AsyncClient, session, admin_user, viewer_user
):
    """Each user's session list is scoped to their own refresh tokens, and a
    cookie belonging to another user never flags any row is_current."""
    admin_access = await _login_with(
        client, user_agent="Admin-Browser", xff="1.1.1.1"
    )
    admin_cookie = client.cookies.get("catlico_refresh")

    # Viewer logs in on the same client — overwrites the shared cookie jar.
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@test.com", "password": "password123"},
        headers={"User-Agent": "Viewer-Browser", "X-Forwarded-For": "2.2.2.2"},
    )
    assert response.status_code == 200
    viewer_access = response.json()["access_token"]
    viewer_cookie = client.cookies.get("catlico_refresh")

    def use_cookie(value: str) -> None:
        client.cookies.clear()
        client.cookies.set(
            "catlico_refresh", value, domain="test.local", path="/api/v1/auth"
        )

    # Admin sees only their own session, flagged current by their own cookie.
    use_cookie(admin_cookie)
    admin_sessions = await _sessions(client, admin_access)
    assert len(admin_sessions) == 1
    assert admin_sessions[0]["user_agent"] == "Admin-Browser"
    assert admin_sessions[0]["is_current"] is True

    # Viewer sees only their own session.
    use_cookie(viewer_cookie)
    viewer_sessions = await _sessions(client, viewer_access)
    assert len(viewer_sessions) == 1
    assert viewer_sessions[0]["user_agent"] == "Viewer-Browser"
    assert viewer_sessions[0]["is_current"] is True

    # Viewer's cookie against admin's list marks nothing current (cross-user guard).
    use_cookie(viewer_cookie)
    cross = await _sessions(client, admin_access)
    assert len(cross) == 1
    assert cross[0]["user_agent"] == "Admin-Browser"
    assert cross[0]["is_current"] is False


# --- Account lockout after repeated failed logins (phase-3 §3.2) ---


async def _wrong_login(client: AsyncClient, email: str = "admin@test.com"):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "definitely-wrong"},
    )


async def _correct_login(client: AsyncClient, email: str = "admin@test.com"):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "password123"},
    )


async def test_login_locks_after_max_attempts(
    client: AsyncClient, session, admin_user, monkeypatch
):
    """The Nth consecutive failed attempt locks the account: locked_until is set
    and the counter resets to 0 so the next window starts fresh."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)

    # First (MAX - 1) failures increment but do not lock.
    for expected in (1, 2):
        r = await _wrong_login(client)
        assert r.status_code == 401
        await session.refresh(admin_user)
        assert admin_user.failed_login_count == expected
        assert admin_user.locked_until is None

    # The 3rd failure locks the account and resets the counter.
    r = await _wrong_login(client)
    assert r.status_code == 401
    await session.refresh(admin_user)
    assert admin_user.failed_login_count == 0
    assert admin_user.locked_until is not None
    assert admin_user.locked_until > datetime.now(UTC)

    # Even the correct password is now rejected while locked.
    r = await _correct_login(client)
    assert r.status_code == 401


async def test_locked_account_response_identical_to_bad_password(
    client: AsyncClient, session, admin_user, viewer_user, monkeypatch
):
    """A locked account's rejection must be byte-identical to an ordinary
    wrong-password rejection (no account-locked oracle)."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)

    # Baseline: an ordinary wrong password on an unlocked account.
    bad = await _wrong_login(client, email="viewer@test.com")
    assert bad.status_code == 401

    # Lock the admin, then present the CORRECT password -> still rejected.
    for _ in range(3):
        await _wrong_login(client)
    locked = await _correct_login(client)

    assert locked.status_code == bad.status_code == 401
    assert locked.json() == bad.json()
    assert locked.content == bad.content


async def test_successful_login_resets_counter(
    client: AsyncClient, session, admin_user, monkeypatch
):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 5)

    for _ in range(2):
        await _wrong_login(client)
    await session.refresh(admin_user)
    assert admin_user.failed_login_count == 2

    r = await _correct_login(client)
    assert r.status_code == 200
    await session.refresh(admin_user)
    assert admin_user.failed_login_count == 0
    assert admin_user.locked_until is None


async def test_nonexistent_email_generic_401_and_no_row(
    client: AsyncClient, session, admin_user
):
    """An unknown email 401s with the same body as a wrong password and never
    creates a user row (no enumeration / no accidental tracking)."""
    bad = await _wrong_login(client)
    assert bad.status_code == 401

    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@test.com", "password": "definitely-wrong"},
    )
    assert unknown.status_code == 401
    assert unknown.json() == bad.json()
    assert unknown.content == bad.content

    # No row was created for the unknown email.
    assert await get_user_by_email(session, "ghost@test.com") is None


async def test_lock_expires_allows_login(
    client: AsyncClient, session, admin_user
):
    """Once locked_until is in the past, login works again."""
    admin_user.locked_until = datetime.now(UTC) - timedelta(minutes=1)
    admin_user.failed_login_count = 0
    session.add(admin_user)
    await session.commit()

    r = await _correct_login(client)
    assert r.status_code == 200


async def test_superadmin_unlock_clears_lock(
    client: AsyncClient, session, admin_user, admin_token, viewer_user, monkeypatch
):
    """A superadmin re-activating the user via PATCH /users/{id} clears the
    lockout state, and the user can log in again."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)

    for _ in range(3):
        await _wrong_login(client, email="viewer@test.com")
    await session.refresh(viewer_user)
    assert viewer_user.locked_until is not None

    r = await client.patch(
        f"/api/v1/users/{viewer_user.id}",
        json={"is_active": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text

    await session.refresh(viewer_user)
    assert viewer_user.failed_login_count == 0
    assert viewer_user.locked_until is None

    ok = await _correct_login(client, email="viewer@test.com")
    assert ok.status_code == 200


async def test_user_locked_audit_event_recorded(
    client: AsyncClient, session, admin_user, monkeypatch
):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "LOGIN_MAX_ATTEMPTS", 3)

    for _ in range(3):
        await _wrong_login(client)

    rows = (
        (
            await session.execute(
                select(Audit).where(
                    Audit.object_type == "user",
                    Audit.object_id == str(admin_user.id),
                    Audit.action == "user.locked",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_unknown_email_pays_bcrypt_cost(session, monkeypatch):
    """Anti-enumeration (timing): the no-user path runs one bcrypt verify against
    the dummy hash, so an unknown email costs the same as a wrong password."""
    import app.crud.user as user_crud

    calls = []
    real_verify = user_crud.verify_password

    def spy(password, hashed):
        calls.append((password, hashed))
        return real_verify(password, hashed)

    monkeypatch.setattr(user_crud, "verify_password", spy)

    result = await user_crud.authenticate_user(session, "ghost@test.com", "whatever")
    assert result is None
    assert len(calls) == 1
    assert calls[0] == ("whatever", user_crud._DUMMY_PASSWORD_HASH)


async def test_locked_account_pays_bcrypt_cost(session, admin_user, monkeypatch):
    """The locked-account path also runs one dummy-hash verify (never the user's
    real hash), equalizing timing with the wrong-password path."""
    import app.crud.user as user_crud

    admin_user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    session.add(admin_user)
    await session.commit()

    calls = []
    real_verify = user_crud.verify_password

    def spy(password, hashed):
        calls.append((password, hashed))
        return real_verify(password, hashed)

    monkeypatch.setattr(user_crud, "verify_password", spy)

    result = await user_crud.authenticate_user(session, "admin@test.com", "password123")
    assert result is None
    assert len(calls) == 1
    # The real (correct) password must NOT be checked against the real hash while
    # locked — only the dummy hash is verified.
    assert calls[0] == ("password123", user_crud._DUMMY_PASSWORD_HASH)
