"""Tests for the enterprise extension seam (plans/phase-3 §3.0).

Two halves:
  * OSS / no-extension — providers empty, capabilities all-false, login unchanged.
  * Fixture-extension — each hook (router, provider, capability, second-factor)
    exercised end-to-end through the live app.
"""

import pytest
from httpx import AsyncClient


@pytest.fixture
def installed_extension():
    """Register the fixture extension in the live registry and mount its routers
    on the app, then tear both back down so no state leaks into other tests."""
    from app.core.extensions import install_extension, registry
    from app.main import app
    from tests.fixtures.extension import fixture_extension

    saved_exts = list(registry.extensions)
    saved_routes = list(app.router.routes)
    install_extension(app, fixture_extension)
    try:
        yield fixture_extension
    finally:
        registry.reset(saved_exts)
        app.router.routes[:] = saved_routes


# --- OSS / no extension installed --------------------------------------------


async def test_auth_providers_empty_in_oss(client: AsyncClient):
    """With no extension installed the login page gets an empty provider list."""
    response = await client.get("/api/v1/auth/providers")
    assert response.status_code == 200
    assert response.json() == []


async def test_system_capabilities_all_false_in_oss(client: AsyncClient):
    response = await client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    assert response.json() == {"sso": False, "mfa": False, "dashboard": False}


async def test_login_unchanged_without_extension(client: AsyncClient, admin_user):
    """The login response is exactly today's Token shape — no mfa_required."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"access_token", "token_type"}
    assert data["token_type"] == "bearer"
    assert "mfa_required" not in data


# --- Fixture extension installed ---------------------------------------------


async def test_fixture_router_is_mounted(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/fixture-ext/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True}


async def test_fixture_provider_advertised(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/auth/providers")
    assert response.status_code == 200
    providers = response.json()
    assert providers == [
        {
            "id": "fixture-oidc",
            "name": "Fixture SSO",
            "kind": "oidc",
            "authorize_path": "/api/v1/fixture-ext/authorize",
        }
    ]


async def test_fixture_capabilities_merged(client: AsyncClient, installed_extension):
    response = await client.get("/api/v1/system/capabilities")
    assert response.status_code == 200
    assert response.json() == {"sso": True, "mfa": True, "dashboard": False}


async def test_login_calls_second_factor_hook(
    client: AsyncClient, admin_user, installed_extension
):
    """When an extension's second_factor_hook returns a challenge, login responds
    with the mfa_required envelope instead of issuing tokens."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["mfa_required"] is True
    assert data["pending_token"] == f"pending-{admin_user.id}"
    assert "access_token" not in data
    # No refresh cookie is set for a challenge (tokens were never issued).
    assert "set-cookie" not in {k.lower() for k in response.headers}


# --- Entry-point loader: resilience + idempotency ----------------------------
#
# A real enterprise extension is discovered via the ``catlico.extensions``
# entry-point group. These tests drive the loader directly (on a fresh registry,
# so nothing leaks into the process-wide singleton) with a monkeypatched
# ``importlib.metadata.entry_points`` to prove: a broken package can't crash
# startup, and repeated loads never double-register.


class _FakeEntryPoint:
    """Minimal stand-in for ``importlib.metadata.EntryPoint`` — just ``name`` and
    a ``load()`` that either returns an extension object or raises."""

    def __init__(self, name, obj=None, exc=None):
        self.name = name
        self._obj = obj
        self._exc = exc

    def load(self):
        if self._exc is not None:
            raise self._exc
        return self._obj


def test_broken_extension_is_skipped_good_one_loads(monkeypatch):
    """A broken/throwing enterprise package must not crash startup: its entry
    point is logged and skipped while a good one still loads."""
    import importlib.metadata

    from app.core.extensions import ExtensionRegistry
    from tests.fixtures.extension import fixture_extension

    bad = _FakeEntryPoint("bad-ext", exc=RuntimeError("boom on import"))
    good = _FakeEntryPoint("good-ext", obj=fixture_extension)

    def fake_entry_points(*, group):
        return [bad, good]

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)

    reg = ExtensionRegistry()
    # Must not raise even though one entry point's load() throws.
    reg.load_from_entry_points()

    # The bad one is skipped; the good one is registered.
    assert reg.extensions == [fixture_extension]


def test_empty_group_is_a_noop_and_load_is_idempotent(monkeypatch):
    """An empty entry-point group is a clean no-op, and loading twice never
    double-registers (idempotency)."""
    import importlib.metadata

    from app.core.extensions import ExtensionRegistry
    from tests.fixtures.extension import fixture_extension

    # Empty group -> clean no-op.
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda *, group: []
    )
    reg = ExtensionRegistry()
    reg.load_from_entry_points()
    assert reg.extensions == []

    # Now a group with one entry point; loading twice registers it exactly once.
    good = _FakeEntryPoint("good-ext", obj=fixture_extension)
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda *, group: [good]
    )
    reg.load_from_entry_points()
    reg.load_from_entry_points()
    assert reg.extensions == [fixture_extension]


# --- Startup hook: run_startup_hooks resilience ------------------------------
#
# An installed extension may need to run STARTUP work (create its owned tables,
# warm a provider cache). catlico-api uses a custom lifespan, so router-level
# on_startup handlers are bypassed; the seam therefore exposes an explicit,
# optional ``async def on_startup(self)`` hook the lifespan awaits. These tests
# drive ``run_startup_hooks`` directly on a fresh registry (nothing leaks into
# the process-wide singleton).


class _StartupExtension:
    """Extension whose async ``on_startup`` records that it ran."""

    def __init__(self, log: list[str], name: str = "ok") -> None:
        self._log = log
        self._name = name

    async def on_startup(self) -> None:
        self._log.append(self._name)


class _BrokenStartupExtension:
    """Extension whose ``on_startup`` raises — must be logged and skipped."""

    async def on_startup(self) -> None:
        raise RuntimeError("boom on startup")


class _NoStartupExtension:
    """Extension that omits ``on_startup`` entirely (a partial extension)."""

    def capabilities(self) -> dict[str, bool]:
        return {}


async def test_run_startup_hooks_awaits_on_startup():
    """A loaded extension's async ``on_startup`` is awaited (side effect runs)."""
    from app.core.extensions import ExtensionRegistry

    log: list[str] = []
    reg = ExtensionRegistry()
    reg.register(_StartupExtension(log))

    await reg.run_startup_hooks()

    assert log == ["ok"]


async def test_run_startup_hooks_noop_on_empty_registry():
    """OSS / no extensions -> run_startup_hooks is a clean no-op."""
    from app.core.extensions import ExtensionRegistry

    reg = ExtensionRegistry()
    # Must not raise and must do nothing.
    await reg.run_startup_hooks()
    assert reg.extensions == []


async def test_extension_without_on_startup_is_fine():
    """An extension that omits on_startup is skipped without error."""
    from app.core.extensions import ExtensionRegistry

    reg = ExtensionRegistry()
    reg.register(_NoStartupExtension())
    # No hook -> nothing to run, no crash.
    await reg.run_startup_hooks()


async def test_broken_on_startup_is_logged_and_others_still_run(monkeypatch):
    """A hook that raises is logged and skipped; other extensions' startup still
    runs and no exception propagates (same resilience contract as discovery).

    caplog / log-handler capture is suppressed by this project's pytest harness,
    so we spy on the module logger's ``exception`` call directly to prove the
    failure is logged."""
    from app.core import extensions as ext_mod
    from app.core.extensions import ExtensionRegistry

    logged: list[str] = []
    monkeypatch.setattr(
        ext_mod.logger,
        "exception",
        lambda msg, *args, **kwargs: logged.append(msg),
    )

    log: list[str] = []
    reg = ExtensionRegistry()
    reg.register(_BrokenStartupExtension())
    reg.register(_StartupExtension(log, name="after-broken"))

    # Must not raise even though the first hook throws.
    await reg.run_startup_hooks()

    # The good extension after the broken one still ran.
    assert log == ["after-broken"]
    # The failure was logged via logger.exception (captures the active traceback).
    assert any("startup" in msg.lower() for msg in logged)


def test_lifespan_wires_run_startup_hooks():
    """The app lifespan must actually call run_startup_hooks. The test client uses
    ASGITransport without a lifespan manager, so the real lifespan never runs
    under pytest; assert the wiring by inspecting the lifespan source instead."""
    import inspect

    import app.main as main_mod

    source = inspect.getsource(main_mod.lifespan)
    assert "run_startup_hooks" in source
