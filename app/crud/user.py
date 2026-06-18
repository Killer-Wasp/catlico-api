import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.security import get_password_hash, verify_password
from app.models.user import User, UserCreate, UserUpdate


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


async def delete_user(session: AsyncSession, db_user: User) -> None:
    await session.delete(db_user)
    await session.commit()


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> User | None:
    user = await get_user_by_email(session, email)
    if not user or not user.hashed_password:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    user.last_login_at = datetime.now(UTC)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
