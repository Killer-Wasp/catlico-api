"""Password reset delivery and token semantics."""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from app.core.security import verify_password
from app.models.auth import PasswordResetToken


async def test_forgot_password_sends_reset_email(client, session, viewer_user, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(email: str, token: str) -> bool:
        sent.append((email, token))
        return True

    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email", fake_send
    )

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "If the email is registered, a reset link has been sent"}
    assert len(sent) == 1
    assert sent[0][0] == viewer_user.email

    token_hash = hashlib.sha256(sent[0][1].encode()).hexdigest()
    row = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
    ).scalar_one_or_none()
    assert row is not None


async def test_forgot_password_unknown_email_does_not_send(client, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(email: str, token: str) -> bool:
        sent.append((email, token))
        return True

    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email", fake_send
    )

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": "missing@example.com"}
    )
    assert resp.status_code == 200
    assert sent == []


async def test_forgot_password_throttles_recent_token(client, session, viewer_user, monkeypatch):
    sent: list[str] = []

    async def fake_send(email: str, token: str) -> bool:
        sent.append(token)
        return True

    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email", fake_send
    )

    for _ in range(2):
        resp = await client.post(
            "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
        )
        assert resp.status_code == 200

    assert len(sent) == 1
    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_forgot_password_keeps_generic_response_when_smtp_fails(
    client, session, viewer_user, monkeypatch
):
    from app.core.configs import settings

    class BrokenSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("smtp down")

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(
        "app.services.password_reset_delivery.smtplib.SMTP",
        BrokenSMTP,
    )

    resp = await client.post(
        "/api/v1/auth/password/forgot", json={"email": viewer_user.email}
    )
    assert resp.status_code == 200
    assert resp.json() == {"message": "If the email is registered, a reset link has been sent"}

    rows = (
        await session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == viewer_user.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_reset_password_single_use(client, session, viewer_user, monkeypatch):
    sent: list[str] = []

    async def fake_send(email: str, token: str) -> bool:
        sent.append(token)
        return True

    monkeypatch.setattr(
        "app.services.password_reset_delivery.send_password_reset_email", fake_send
    )

    await client.post("/api/v1/auth/password/forgot", json={"email": viewer_user.email})
    token = sent[0]

    ok = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "new-password-123"},
    )
    assert ok.status_code == 200
    await session.refresh(viewer_user)
    assert verify_password("new-password-123", viewer_user.hashed_password)

    again = await client.post(
        "/api/v1/auth/password/reset",
        json={"token": token, "new_password": "another-password-123"},
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
        json={"token": raw, "new_password": "new-password-123"},
    )
    assert resp.status_code == 400
