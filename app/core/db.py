from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.configs import settings

engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI, echo=settings.DB_ECHO)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Unit-of-work: one transaction per request. Commits once if the handler
    returns without raising, rolls back otherwise. This is what lets a mutation
    and its audit + outbox rows (written by app.crud.audit.record_audit) commit
    atomically — CRUD must not commit."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def ensure_default_superadmin(session: AsyncSession) -> None:
    from app.core.security import get_password_hash, verify_password
    from app.crud.user import get_user_by_email
    from app.models.user import User

    user = await get_user_by_email(session, settings.DEFAULT_ADMIN_EMAIL)
    if user is None:
        session.add(
            User(
                email=settings.DEFAULT_ADMIN_EMAIL.lower().strip(),
                hashed_password=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
                is_superadmin=True,
                is_active=True,
            )
        )
        await session.commit()
        return

    if not user.hashed_password or not verify_password(
        settings.DEFAULT_ADMIN_PASSWORD, user.hashed_password
    ):
        user.hashed_password = get_password_hash(settings.DEFAULT_ADMIN_PASSWORD)
        session.add(user)
        await session.commit()


async def init_db(session: AsyncSession) -> None:
    from app.crud.role import upsert_builtin_role
    from app.models.observable import BUILTIN_OBSERVABLE_TYPES, ObservableType
    from app.models.role import BUILTIN_ROLES

    # Seed built-in roles (idempotent)
    for name, permissions in BUILTIN_ROLES.items():
        await upsert_builtin_role(session, name, permissions, created_by="system")

    # Seed built-in observable types (idempotent)
    for type_name, is_attachment in BUILTIN_OBSERVABLE_TYPES.items():
        if await session.get(ObservableType, type_name) is None:
            session.add(ObservableType(name=type_name, is_attachment=is_attachment))
    await session.commit()
    await ensure_default_superadmin(session)
