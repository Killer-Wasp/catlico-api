import asyncio
import atexit
import json
import os
import shutil
import subprocess
import time
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer


def _ensure_docker_host() -> None:
    """Point the Docker SDK at the daemon. The SDK only checks DOCKER_HOST and the
    default socket, so on setups that use a non-default socket (Rancher Desktop,
    Colima, ...) fall back to the active `docker context` endpoint."""
    if os.environ.get("DOCKER_HOST") or os.path.exists("/var/run/docker.sock"):
        return
    docker = shutil.which("docker")
    if not docker:
        return
    try:
        out = subprocess.check_output([docker, "context", "inspect"], text=True)
        os.environ["DOCKER_HOST"] = json.loads(out)[0]["Endpoints"]["docker"]["Host"]
    except Exception:
        pass


# --- Postgres test container -------------------------------------------------
# Spin up a throwaway Postgres *before* any app module is imported, so the
# required DATABASE_URL is set before app.core.db builds its engine. Tests run
# against the same engine as dev/prod, and the schema is built by the real
# Alembic migration chain, so the migrations themselves get exercised.
_ensure_docker_host()
# Reap the container ourselves (atexit) instead of via Ryuk, which is brittle when
# the daemon socket lives at a non-default path. atexit (vs pytest_sessionfinish)
# also covers a failure during conftest import, so the container can't leak.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
_PG = PostgresContainer("postgres:16", driver="asyncpg")
_PG.start()
atexit.register(_PG.stop)
_DB_URL = _PG.get_connection_url()
os.environ["DATABASE_URL"] = _DB_URL


async def _probe(url: str) -> None:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await eng.dispose()


def _wait_for_db(url: str, timeout: float = 30.0) -> None:
    """testcontainers' readiness probe can return before Postgres actually accepts
    connections (the async driver has no sync wait), so block until a real query
    succeeds before running migrations."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            asyncio.run(_probe(url))
            return
        except Exception as exc:  # noqa: BLE001 — retry any connection error
            last = exc
            time.sleep(0.25)
    raise RuntimeError(f"Postgres not ready after {timeout}s: {last}")


_wait_for_db(_DB_URL)

# App imports must come after DATABASE_URL is set above.
import app.models  # noqa: E402, F401 — registers all table models with SQLModel metadata
from app.core.db import get_session, run_migrations  # noqa: E402
from app.core.security import TokenPayload, create_access_token  # noqa: E402
from app.crud.organisation import create_organisation  # noqa: E402
from app.crud.organisation_member import add_member  # noqa: E402
from app.crud.role import upsert_builtin_role  # noqa: E402
from app.crud.user import create_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.organisation import OrganisationCreate  # noqa: E402
from app.models.organisation_member import OrganisationMemberCreate  # noqa: E402
from app.models.role import BUILTIN_ROLES  # noqa: E402
from app.models.user import UserCreate  # noqa: E402

# Build the schema once via the real migrations, then capture the table list used
# to reset state between tests.
run_migrations()


async def _list_tables() -> list[str]:
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
            return [r[0] for r in rows]
    finally:
        await eng.dispose()


# observable_type holds built-in reference data that production seeds via init_db
# and that nothing mutates at runtime. Seed it once and keep it across tests
# (exclude it from truncation) so observable FKs resolve — mirrors prod.
_TABLES = [t for t in asyncio.run(_list_tables()) if t != "observable_type"]
_TRUNCATE_SQL = (
    "TRUNCATE " + ", ".join(f'"{t}"' for t in _TABLES) + " RESTART IDENTITY CASCADE"
)


async def _seed_reference_data() -> None:
    from app.models.observable import BUILTIN_OBSERVABLE_TYPES, ObservableType

    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            for name, is_attachment in BUILTIN_OBSERVABLE_TYPES.items():
                s.add(ObservableType(name=name, is_attachment=is_attachment))
            await s.commit()
    finally:
        await eng.dispose()


asyncio.run(_seed_reference_data())


@pytest.fixture(autouse=True)
async def _reset_db() -> AsyncGenerator[None, None]:
    """Reset every table after each test for isolation. The schema (built once by
    the migrations) persists; only row data is wiped."""
    yield
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.execute(text(_TRUNCATE_SQL))
    finally:
        await eng.dispose()


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    from app.core.configs import settings

    monkeypatch.setattr(settings, "SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
async def engine() -> AsyncGenerator:
    # NullPool: no connection is reused across tests, so asyncpg connections never
    # leak across pytest-asyncio's per-test event loops.
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def client(session, tmp_path) -> AsyncGenerator[AsyncClient, None]:
    from app.core.storage import BlobStorage, get_storage

    async def override_get_session():
        # Commit on success so separate verification sessions observe the writes
        # (on Postgres, uncommitted writes aren't visible across sessions). We
        # deliberately do NOT roll back on error: the app pre-checks conflicts and
        # never lets an IntegrityError reach the session, so failures are
        # app-level HTTPExceptions that leave the transaction usable. Rolling back
        # this *shared* test session would expire the test's ORM objects and break
        # later attribute access. On error the commit below is simply skipped.
        yield session
        await session.commit()

    # Isolated local blob storage per test — no SeaweedFS/S3 needed in CI.
    test_storage = BlobStorage("local", str(tmp_path / "blobs"))

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_storage] = lambda: test_storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def runner_secret(monkeypatch):
    """Configure the platform-level plugin-runner shared secret for the test."""
    from app.core.configs import settings

    monkeypatch.setattr(settings, "PLUGIN_RUNNER_SHARED_SECRET", "test-runner-secret")
    return "test-runner-secret"


@pytest.fixture
async def admin_user(session):
    return await create_user(
        session,
        UserCreate(first_name="Test", last_name="User", email="admin@test.com", password="password123", is_superadmin=True),
    )


@pytest.fixture
async def viewer_user(session):
    return await create_user(
        session,
        UserCreate(first_name="Test", last_name="User", email="viewer@test.com", password="password123"),
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
def observable_types():
    """Built-in observable types are pre-seeded once and persist across tests (see
    _seed_reference_data); this fixture just exposes the mapping."""
    from app.models.observable import BUILTIN_OBSERVABLE_TYPES

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
        UserCreate(first_name="Test", last_name="User", email="analyst-a@test.com", password="password123"),
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
        UserCreate(first_name="Test", last_name="User", email="analyst-b@test.com", password="password123"),
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
        UserCreate(first_name="Test", last_name="User", email="ro-a@test.com", password="password123"),
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
