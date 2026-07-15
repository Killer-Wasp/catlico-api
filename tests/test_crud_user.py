import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user import (
    authenticate_user,
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
    get_users,
    set_password,
    update_user,
)
from app.models.user import UserCreate, UserUpdate


async def test_create_user(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="test@example.com"))
    assert user.id is not None
    assert user.email == "test@example.com"
    # Always created password-less; the credential is set later (invite / self-service).
    assert user.hashed_password is None
    assert user.is_superadmin is False
    assert user.is_active is True
    assert user.must_change_password is False


async def test_create_user_normalizes_email(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="  TEST@EXAMPLE.COM  "))
    assert user.email == "test@example.com"


async def test_create_user_no_password(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="nopass@example.com"))
    assert user.hashed_password is None


async def test_get_user_by_email(session: AsyncSession):
    await create_user(session, UserCreate(first_name="Test", last_name="User", email="find@example.com"))
    user = await get_user_by_email(session, "find@example.com")
    assert user is not None
    assert user.email == "find@example.com"


async def test_get_user_by_email_case_insensitive(session: AsyncSession):
    await create_user(session, UserCreate(first_name="Test", last_name="User", email="case@example.com"))
    user = await get_user_by_email(session, "CASE@EXAMPLE.COM")
    assert user is not None


async def test_get_user_by_email_not_found(session: AsyncSession):
    user = await get_user_by_email(session, "nobody@example.com")
    assert user is None


async def test_get_user_by_id(session: AsyncSession):
    created = await create_user(session, UserCreate(first_name="Test", last_name="User", email="byid@example.com"))
    user = await get_user_by_id(session, created.id)
    assert user is not None
    assert user.id == created.id


async def test_get_user_by_id_not_found(session: AsyncSession):
    user = await get_user_by_id(session, uuid.uuid4())
    assert user is None


async def test_get_users(session: AsyncSession):
    for i in range(3):
        await create_user(session, UserCreate(first_name="Test", last_name="User", email=f"user{i}@example.com"))
    users = await get_users(session)
    assert len(users) == 3


async def test_get_users_pagination(session: AsyncSession):
    for i in range(5):
        await create_user(session, UserCreate(first_name="Test", last_name="User", email=f"page{i}@example.com"))
    page = await get_users(session, skip=2, limit=2)
    assert len(page) == 2


async def test_update_user_email(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="old@example.com"))
    updated = await update_user(session, user, UserUpdate(email="new@example.com"))
    assert updated.email == "new@example.com"


async def test_update_user_email_normalized(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="norm@example.com"))
    updated = await update_user(session, user, UserUpdate(email="NORM2@EXAMPLE.COM"))
    assert updated.email == "norm2@example.com"


async def test_set_password_sets_hash(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="pw@example.com"))
    assert user.hashed_password is None
    updated = await set_password(session, user, "a-fresh-password")
    assert updated.hashed_password is not None
    assert updated.hashed_password != "a-fresh-password"


async def test_set_password_clears_must_change_flag(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="force@example.com"))
    user.must_change_password = True
    session.add(user)
    await session.commit()
    await session.refresh(user)
    updated = await set_password(session, user, "a-fresh-password")
    assert updated.must_change_password is False


async def test_update_user_ignores_password_field(session: AsyncSession):
    # UserUpdate no longer carries a password; an admin PATCH can never set one.
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="nopw-admin@example.com"))
    await set_password(session, user, "original-password")
    original_hash = user.hashed_password
    # `password` is not a field on UserUpdate; it is silently ignored.
    updated = await update_user(session, user, UserUpdate(first_name="Renamed"))
    assert updated.hashed_password == original_hash
    assert updated.first_name == "Renamed"


async def test_update_user_superadmin(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="promote@example.com"))
    assert user.is_superadmin is False
    updated = await update_user(session, user, UserUpdate(is_superadmin=True))
    assert updated.is_superadmin is True


async def test_update_user_deactivate(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="active@example.com"))
    updated = await update_user(session, user, UserUpdate(is_active=False))
    assert updated.is_active is False


async def test_delete_user(session: AsyncSession):
    user = await create_user(session, UserCreate(first_name="Test", last_name="User", email="del@example.com"))
    await delete_user(session, user)
    assert await get_user_by_id(session, user.id) is None


async def test_authenticate_user_success(session: AsyncSession):
    created = await create_user(session, UserCreate(first_name="Test", last_name="User", email="auth@example.com"))
    await set_password(session, created, "secret")
    user = await authenticate_user(session, "auth@example.com", "secret")
    assert user is not None
    assert user.last_login_at is not None


async def test_authenticate_user_updates_last_login(session: AsyncSession):
    created = await create_user(session, UserCreate(first_name="Test", last_name="User", email="login@example.com"))
    await set_password(session, created, "pass-that-is-long")
    user = await authenticate_user(session, "login@example.com", "pass-that-is-long")
    assert user is not None
    assert user.last_login_at is not None


async def test_authenticate_user_wrong_password(session: AsyncSession):
    created = await create_user(session, UserCreate(first_name="Test", last_name="User", email="wrong@example.com"))
    await set_password(session, created, "secret")
    user = await authenticate_user(session, "wrong@example.com", "bad")
    assert user is None


async def test_authenticate_user_not_found(session: AsyncSession):
    user = await authenticate_user(session, "ghost@example.com", "secret")
    assert user is None


async def test_authenticate_user_no_password(session: AsyncSession):
    await create_user(session, UserCreate(first_name="Test", last_name="User", email="nopass@example.com"))
    user = await authenticate_user(session, "nopass@example.com", "anything")
    assert user is None
