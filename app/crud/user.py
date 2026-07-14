import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.core.security import get_password_hash, verify_password
from app.models.organisation_member import OrganisationMember
from app.models.user import User, UserCreate, UserUpdate


# Precomputed hash used to equalize timing on the login failure paths: every
# failure mode (unknown email, passwordless/SSO account, locked account, wrong
# password) runs one bcrypt verify, so an unknown email cannot be distinguished
# from a known one by response time (no enumeration oracle).
_DUMMY_PASSWORD_HASH = get_password_hash("catlico-dummy-password")


def _normalize_email(email: str) -> str:
    return email.lower().strip()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(
        select(User).where(User.email == _normalize_email(email))
    )
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_users(session: AsyncSession, skip: int = 0, limit: int = 100) -> list[User]:
    result = await session.execute(select(User).offset(skip).limit(limit))
    return list(result.scalars().all())


async def search_users(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    organisation_id: str | None = None,
) -> list[User]:
    """Case-insensitive search over email, first_name and last_name for the
    assignee picker. A query matching any of the three fields returns the user;
    also matches a "first last" full-name query. Only active users are returned.
    Blank query returns the first `limit` active users.

    When `organisation_id` is given, results are limited to members of that org —
    the assignee picker uses this so it only offers users who can actually be
    assigned (assignment requires org membership)."""
    stmt = select(User).where(User.is_active == True)  # noqa: E712
    if organisation_id is not None:
        stmt = stmt.join(
            OrganisationMember, OrganisationMember.user_id == User.id
        ).where(OrganisationMember.organisation_id == organisation_id)
    q = query.strip()
    if q:
        like = f"%{q.lower()}%"
        full_name = func.lower(
            func.trim(
                func.concat(
                    func.coalesce(User.first_name, ""),
                    " ",
                    func.coalesce(User.last_name, ""),
                )
            )
        )
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(like),
                func.lower(User.first_name).like(like),
                func.lower(User.last_name).like(like),
                full_name.like(like),
            )
        )
    stmt = stmt.order_by(User.email).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def emails_for_ids(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Bulk id → email lookup. Used to resolve assignees for a page of cases."""
    if not user_ids:
        return {}
    result = await session.execute(
        select(User.id, User.email).where(User.id.in_(user_ids))
    )
    return {uid: email for uid, email in result.all()}


async def create_user(session: AsyncSession, user_in: UserCreate) -> User:
    db_user = User(
        email=_normalize_email(str(user_in.email)),
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        is_superadmin=user_in.is_superadmin,
        is_active=True,
        hashed_password=get_password_hash(user_in.password) if user_in.password else None,
    )
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def update_user(session: AsyncSession, db_user: User, user_in: UserUpdate) -> User:
    update_data = user_in.model_dump(exclude_unset=True)
    if "password" in update_data:
        raw = update_data.pop("password")
        update_data["hashed_password"] = get_password_hash(raw) if raw else None
    if "email" in update_data:
        update_data["email"] = _normalize_email(str(update_data["email"]))
    update_data["updated_at"] = datetime.now(UTC)
    db_user.sqlmodel_update(update_data)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def set_avatar(
    session: AsyncSession, db_user: User, attachment_id: uuid.UUID | None
) -> User:
    """Point the user's profile picture at a content-addressed blob (or clear it
    with None). The blob itself lives in the shared attachment store."""
    db_user.avatar_attachment_id = attachment_id
    db_user.updated_at = datetime.now(UTC)
    session.add(db_user)
    await session.commit()
    await session.refresh(db_user)
    return db_user


async def delete_user(session: AsyncSession, db_user: User) -> None:
    await session.delete(db_user)
    await session.commit()


async def clear_lockout(session: AsyncSession, db_user: User) -> User:
    """Reset a user's failed-login counter and unlock them. Used by the superadmin
    user-admin path (re-activating / setting a password unlocks the account)."""
    db_user.failed_login_count = 0
    db_user.locked_until = None
    session.add(db_user)
    await session.flush()
    return db_user


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> User | None:
    """Verify local-password credentials, applying account lockout.

    Returns the user on success (resetting any accumulated failures) and None on
    every failure mode — unknown email, passwordless/SSO account, wrong password,
    or a currently-locked account. All failure modes return None so the caller's
    401 is identical and cannot be used to enumerate accounts or detect a lock;
    the real reason is only recorded in the audit log.

    Side effects on failure are committed here (not left to the request's
    unit-of-work) because the login route raises a 401, which would otherwise roll
    the failed-attempt increment back.
    """
    from app.crud.audit import record_audit

    user = await get_user_by_email(session, email)
    # Anti-enumeration: an unknown email or an account with no local password
    # (SSO/passwordless) is never tracked, locked, or created — just a generic fail.
    # Run one bcrypt verify against a dummy hash so this path costs the same as a
    # wrong-password path: an unknown email is not distinguishable by timing.
    if not user or not user.hashed_password:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    now = datetime.now(UTC)
    # A currently-locked account is rejected BEFORE the real password is evaluated,
    # and indistinguishably from a wrong password — in body AND timing (the dummy
    # verify keeps the bcrypt cost identical to the wrong-password branch).
    locked_until = user.locked_until
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until > now:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            return None

    if not verify_password(password, user.hashed_password):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.LOGIN_MAX_ATTEMPTS:
            # Lock the account and reset the counter so the next window (after the
            # lock expires) starts fresh.
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            user.failed_login_count = 0
            await record_audit(
                session,
                action="user.locked",
                obj=user,
                actor="system",
                details={
                    "event": "account_locked",
                    "locked_until": user.locked_until.isoformat(),
                    "lockout_minutes": settings.LOGIN_LOCKOUT_MINUTES,
                },
            )
        session.add(user)
        await session.commit()
        return None

    # Success: clear any accumulated failures / lock and stamp the login.
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
