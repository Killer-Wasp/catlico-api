from app.core.db import ensure_default_superadmin
from app.crud.user import get_user_by_email
from app.models.user import User


async def test_ensure_default_superadmin_creates_missing_user(session, monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "DEFAULT_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "secret123")

    await ensure_default_superadmin(session)
    user = await get_user_by_email(session, "root@example.com")
    assert user is not None
    assert user.is_superadmin is True
    assert user.is_active is True
    assert user.hashed_password


async def test_ensure_default_superadmin_rotates_password_when_changed(session, monkeypatch):
    from app.core.configs import settings
    from app.core.security import get_password_hash

    monkeypatch.setattr(settings, "DEFAULT_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "secret123")

    user = User(
        email="root@example.com",
        hashed_password=get_password_hash("old-password"),
        is_superadmin=True,
        is_active=True,
    )
    old_hash = user.hashed_password
    session.add(user)
    await session.commit()

    await ensure_default_superadmin(session)
    updated = await get_user_by_email(session, "root@example.com")
    assert updated is not None
    assert updated.hashed_password != old_hash
