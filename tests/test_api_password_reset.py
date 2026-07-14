"""Password reset flow: delivery, token semantics, and security behaviors."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.core.security import verify_password
from app.crud.auth import issue_refresh_token
from app.models.audit import Audit
from app.models.auth import PasswordResetToken, RefreshToken

GENERIC = {"message": "If the email is registered, a reset link has been sent"}
VALID_PASSWORD = "a-brand-new-password"  # >= 12 chars


def _fake_sender(sink: list[tuple[str, str]]):
    async def fake_send(email: str, token: str) -> bool:
        sink.append((email, token))
        return True

    return fake_send


async def test_forgot_password_sends_reset_email(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert len(sent) == 1 and sent[0][0] == viewer_user.email

    token_hash = hashlib.sha256(sent[0][1].encode()).hexdigest()
    row = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    assert row is not None


async def test_forgot_password_unknown_email_is_indistinguishable(client, monkeypatch):
    """Unknown email: same status + body, and nothing sent (no enumeration)."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": "missing@example.com"}
    )
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert sent == []


async def test_forgot_password_throttles_recent_token(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )

    for _ in range(2):
        resp = await client.post(
            "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
        )
        assert resp.status_code == 200

    assert len(sent) == 1  # second request throttled
    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_forgot_password_generic_response_when_smtp_fails(
    client, session, viewer_user, monkeypatch
):
    from app.core.configs import settings

    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("smtp down")

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr("app.services.smtp.smtplib.SMTP", BrokenSMTP)

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    # Token is still created even though delivery failed.
    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_forgot_password_records_audit(client, session, viewer_user, monkeypatch):
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender([]),
    )
    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})

    audits = (
        await session.execute(
            select(Audit).where(
                Audit.object_type == "user",
                Audit.object_id == str(viewer_user.id),
            )
        )
    ).scalars().all()
    events = [a.details.get("event") for a in audits if a.details]
    assert "password_reset_requested" in events


async def test_reset_password_single_use(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0][1]

    ok = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": VALID_PASSWORD},
    )
    assert ok.status_code == 200
    await session.refresh(viewer_user)
    assert verify_password(VALID_PASSWORD, viewer_user.hashed_password)

    again = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "another-good-password"},
    )
    assert again.status_code == 400


async def test_reset_password_expired_token_rejected(client, session, viewer_user):
    raw = "expired-token"
    session.add(
        PasswordResetToken(
            user_id=viewer_user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await session.flush()

    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": raw, "new_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400


async def test_reset_password_invalid_token_rejected(client, viewer_user):
    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": "never-issued", "new_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400


async def test_reset_password_rejects_short_password(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0][1]

    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "short"},
    )
    assert resp.status_code == 400
    # Password unchanged and the token is still usable (not consumed on a weak try).
    await session.refresh(viewer_user)
    assert not verify_password("short", viewer_user.hashed_password)
    ok = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": VALID_PASSWORD},
    )
    assert ok.status_code == 200


async def test_reset_password_revokes_all_sessions(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    # Two active sessions for the user.
    await issue_refresh_token(session, viewer_user.id)
    await issue_refresh_token(session, viewer_user.id)

    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0][1]
    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": VALID_PASSWORD},
    )
    assert resp.status_code == 200

    remaining = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert remaining == []


async def test_reset_password_records_audit(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0][1]
    await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": VALID_PASSWORD},
    )

    audits = (
        await session.execute(
            select(Audit).where(
                Audit.object_type == "user",
                Audit.object_id == str(viewer_user.id),
            )
        )
    ).scalars().all()
    events = [a.details.get("event") for a in audits if a.details]
    assert "password_reset_completed" in events


async def test_forgot_password_inactive_user_gets_nothing(
    client, session, viewer_user, monkeypatch
):
    """Deactivated accounts must be inert: no email, no token, same response."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    viewer_user.is_active = False
    session.add(viewer_user)
    await session.flush()

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    assert resp.json() == GENERIC
    assert sent == []
    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert rows == []


async def test_reset_password_rejected_for_deactivated_user(
    client, session, viewer_user, monkeypatch
):
    """A token issued while active must stop working once the account is disabled."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0][1]

    viewer_user.is_active = False
    session.add(viewer_user)
    await session.flush()

    resp = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": VALID_PASSWORD},
    )
    assert resp.status_code == 400


async def test_forgot_password_purges_dead_tokens(client, session, viewer_user, monkeypatch):
    """Minting a token drops the user's used/expired rows (opportunistic cleanup)."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email",
        _fake_sender(sent),
    )
    # Seed one used and one expired row.
    session.add(
        PasswordResetToken(
            user_id=viewer_user.id,
            token_hash="dead-used",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            used_at=datetime.now(UTC),
        )
    )
    session.add(
        PasswordResetToken(
            user_id=viewer_user.id,
            token_hash="dead-expired",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            # Old enough to clear the per-user throttle window (which only
            # considers unused tokens minted recently).
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await session.flush()

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    # Only the freshly minted token remains.
    assert len(rows) == 1 and rows[0].used_at is None


async def test_user_create_enforces_password_policy(client, admin_token):
    resp = await client.post(
        "/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "short-pw@test.com",
            "password": "short",
            "first_name": "Short",
            "last_name": "Password",
        },
    )
    assert resp.status_code == 400
    assert "at least 12 characters" in resp.json()["detail"]


async def test_me_update_enforces_password_policy(client, viewer_token):
    resp = await client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"current_password": "password123", "new_password": "short"},
    )
    assert resp.status_code == 400
    assert "at least 12 characters" in resp.json()["detail"]
