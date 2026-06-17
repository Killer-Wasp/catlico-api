from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import app.models  # noqa: F401 — registers all table models with SQLModel metadata
from app.core.db import get_session
from app.core.security import TokenPayload, create_access_token
from app.crud.organisation import create_organisation
from app.crud.organisation_member import add_member
from app.crud.role import upsert_builtin_role
from app.crud.user import create_user
from app.main import app
from app.models.organisation import OrganisationCreate
from app.models.organisation_member import OrganisationMemberCreate
from app.models.role import BUILTIN_ROLES
from app.models.user import UserCreate

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core.configs import settings

    monkeypatch.setattr(settings, "SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
async def engine():
    _engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def client(session, tmp_path) -> AsyncGenerator[AsyncClient, None]:
    from app.core.storage import BlobStorage, get_storage

    async def override_get_session():
        yield session

    # Isolated local blob storage per test — no SeaweedFS/S3 needed in CI.
    test_storage = BlobStorage("local", str(tmp_path / "blobs"))

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_storage] = lambda: test_storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def analyzer_secret(monkeypatch):
    """Configure the platform-level analyzer shared secret for the test."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "ANALYZER_SHARED_SECRET", "test-analyzer-secret")
    return "test-analyzer-secret"


@pytest.fixture
async def admin_user(session):
    return await create_user(
        session,
        UserCreate(email="admin@test.com", password="password123", is_superadmin=True),
    )


@pytest.fixture
async def viewer_user(session):
    return await create_user(
        session,
        UserCreate(email="viewer@test.com", password="password123"),
    )


@pytest.fixture
def admin_token(admin_user):
    return create_access_token(
        TokenPayload(user_id=admin_user.id, is_superadmin=True, organisations=[])
    )


@pytest.fixture
def viewer_token(viewer_user):
    return create_access_token(
        TokenPayload(user_id=viewer_user.id, is_superadmin=False, organisations=[])
    )


# --- Cases milestone fixtures ---


@pytest.fixture
async def builtin_roles(session):
    """Seed built-in roles (org-admin, analyst, read-only)."""
    roles = {}
    for name, perms in BUILTIN_ROLES.items():
        roles[name] = await upsert_builtin_role(session, name, perms, created_by="system")
    return roles


@pytest.fixture
async def observable_types(session):
    """Seed built-in observable types."""
    from app.models.observable import BUILTIN_OBSERVABLE_TYPES, ObservableType

    for name, is_attachment in BUILTIN_OBSERVABLE_TYPES.items():
        session.add(ObservableType(name=name, is_attachment=is_attachment))
    await session.commit()
    return BUILTIN_OBSERVABLE_TYPES


@pytest.fixture
async def org_a(session, admin_user):
    return await create_organisation(
        session,
        OrganisationCreate(id="org-a", name="Org A"),
        created_by=str(admin_user.id),
    )


@pytest.fixture
async def org_b(session, admin_user):
    return await create_organisation(
        session,
        OrganisationCreate(id="org-b", name="Org B"),
        created_by=str(admin_user.id),
    )


@pytest.fixture
async def analyst_a(session, org_a, builtin_roles, admin_user):
    """A user who is org-admin of org-a."""
    user = await create_user(
        session,
        UserCreate(email="analyst-a@test.com", password="password123"),
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["org-admin"].id),
        created_by=str(admin_user.id),
    )
    return user


@pytest.fixture
def analyst_a_token(analyst_a, org_a):
    return create_access_token(
        TokenPayload(
            user_id=analyst_a.id, is_superadmin=False, organisations=[org_a.id]
        )
    )


@pytest.fixture
async def analyst_b(session, org_b, builtin_roles, admin_user):
    user = await create_user(
        session,
        UserCreate(email="analyst-b@test.com", password="password123"),
    )
    await add_member(
        session,
        org_b.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["org-admin"].id),
        created_by=str(admin_user.id),
    )
    return user


@pytest.fixture
def analyst_b_token(analyst_b, org_b):
    return create_access_token(
        TokenPayload(
            user_id=analyst_b.id, is_superadmin=False, organisations=[org_b.id]
        )
    )


@pytest.fixture
async def readonly_a(session, org_a, builtin_roles, admin_user):
    """A read-only user in org-a (no write:case)."""
    user = await create_user(
        session,
        UserCreate(email="ro-a@test.com", password="password123"),
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["read-only"].id),
        created_by=str(admin_user.id),
    )
    return user


@pytest.fixture
def readonly_a_token(readonly_a, org_a):
    return create_access_token(
        TokenPayload(
            user_id=readonly_a.id, is_superadmin=False, organisations=[org_a.id]
        )
    )
