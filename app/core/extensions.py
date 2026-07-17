"""Enterprise extension seam (plans/phase-3 §3.0).

This is the OSS-side foundation the private ``catlico-enterprise`` package plugs
into. With NO extension installed the seam is inert: no routers are mounted, the
identity-provider list is empty, capabilities are all-false, and the login
second-factor hook returns ``None`` — so OSS behaviour is byte-for-byte
unchanged. An installed extension (discovered via the ``catlico.extensions``
entry-point group) can:

  * mount its own routers under the versioned API,
  * advertise SSO identity providers to the login page,
  * gate login behind a second factor (MFA),
  * veto a core write it has a governance policy about (case status transitions),
  * flip capability flags the web reads to decide whether to render enterprise UI.

Mostly-additive, with named veto points. The seam is additive by default — an
extension adds routers/providers/capabilities. The exceptions are the *policy
hooks* (``second_factor_hook``, ``case_transition_hook``), which let an extension
deny something the OSS core would otherwise allow. These are deliberately
enumerated rather than general: each is a named hook on a specific core write, so
the set of places an extension can say "no" stays greppable. A hook an extension
omits is skipped, and an absent hook always means *allow* — never *deny* — so the
OSS-unchanged guarantee holds by construction.

Extension interface (all hooks OPTIONAL — a partial extension is fine; the
registry duck-types each hook and skips any that a given extension omits):

    class Extension(Protocol):
        def routers(self) -> list[APIRouter]: ...
        def identity_providers(self) -> list[IdentityProvider]: ...
        def second_factor_hook(self, user) -> Challenge | None: ...   # may be async
        def case_transition_hook(self, **kw) -> Denial | None: ...    # may be async
        def capabilities(self) -> dict[str, bool]: ...
        async def on_startup(self) -> None: ...   # run once at app boot

``on_startup`` lets an extension run startup work (create its owned tables, warm
a provider cache). catlico-api uses a custom lifespan, so router-level
``on_startup`` handlers are bypassed — the lifespan awaits
:meth:`ExtensionRegistry.run_startup_hooks` explicitly instead. It runs AFTER
catlico-api's own DB init/migrations, so an extension can create/query its own
tables; a hook that raises is logged and skipped so a broken extension can't
crash the OSS app boot.

Router mount convention: every router an extension returns is mounted under the
``/api/v1`` prefix (the same namespace as the core API), so an extension router
declaring ``prefix="/sso"`` is served at ``/api/v1/sso/...``.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import logging
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from fastapi import APIRouter
from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.api.deps import AuthContext
    from app.models.case_ import Case
    from app.models.case_status import CaseStatusRef
    from app.models.user import User

logger = logging.getLogger(__name__)

#: The setuptools/importlib entry-point group extensions register themselves under.
ENTRY_POINT_GROUP = "catlico.extensions"

#: Prefix every extension router is mounted under (alongside the core v1 API).
EXTENSION_ROUTER_PREFIX = "/api/v1"

#: The capability keys the platform always reports. An extension may flip these
#: to ``True`` or add its own keys; OSS reports them all-false.
DEFAULT_CAPABILITIES: dict[str, bool] = {
    "sso": False,
    "mfa": False,
    "dashboard": False,
    #: Case status-transition governance (org-defined who-may-close rules). Unlike
    #: ``dashboard`` (install-gated), this MUST be flipped by *configured rules*, the
    #: way ``sso``/``mfa`` are — merely installing the enterprise package must never
    #: start rejecting transitions that worked yesterday. See
    #: docs/case-statuses-tiering-decision.md §4.
    "case_workflow": False,
}


class IdentityProvider(BaseModel):
    """A single SSO provider advertised to the (pre-auth) login page.

    Just enough for the web to render an SSO button and know where to send the
    browser to begin the flow.
    """

    #: Stable identifier, e.g. "okta". Used by the web as a key / in the callback.
    id: str
    #: Human-readable label for the login button, e.g. "Log in with Okta".
    name: str
    #: Federation protocol.
    kind: Literal["oidc", "saml"]
    #: Absolute path the browser is sent to in order to start the flow.
    authorize_path: str


class Challenge(BaseModel):
    """A second-factor challenge returned by ``second_factor_hook``.

    When present, the login route responds with an ``mfa_required`` envelope
    carrying ``pending_token`` instead of issuing access/refresh tokens. The
    actual MFA verification + token exchange is built by ``catlico-enterprise``;
    OSS only defines this contract and never produces a challenge.
    """

    #: Challenge type, e.g. "totp".
    kind: str
    #: Opaque token the client returns when completing the second factor.
    pending_token: str


class Denial(BaseModel):
    """A policy hook's veto of a core write. The calling route turns it into a 403.

    Returning ``None`` from a policy hook means *allow*; only an explicit ``Denial``
    blocks. OSS never produces one — with no extension installed no hook exists to
    call, so every write the core would allow stays allowed.
    """

    #: Human-readable, surfaced to the client as the 403 detail. This text is the
    #: point of the feature ("Only a team lead may resolve a case"), so it should
    #: name the rule, not restate the status codes.
    reason: str
    #: Stable machine-readable discriminator, e.g. "transition_forbidden".
    code: str


@runtime_checkable
class Extension(Protocol):
    """Structural type documenting the full extension surface. Every method is
    optional at runtime — the registry checks for each hook before calling it."""

    def routers(self) -> list[APIRouter]: ...

    def identity_providers(self) -> list[IdentityProvider]: ...

    def second_factor_hook(self, user: User) -> Challenge | None: ...

    def case_transition_hook(
        self,
        *,
        case: Case,
        from_status: CaseStatusRef,
        to_status: CaseStatusRef,
        actor: AuthContext,
    ) -> Denial | None: ...

    def capabilities(self) -> dict[str, bool]: ...

    async def on_startup(self) -> None: ...


class ExtensionRegistry:
    """Holds the loaded extensions and aggregates their hooks. In OSS (no
    extensions) every aggregate is empty / all-false / ``None``."""

    def __init__(self) -> None:
        self._extensions: list[Extension] = []
        #: Names of entry points already loaded, so repeated
        #: ``load_from_entry_points`` calls (module re-import, app re-creation)
        #: are idempotent no-ops rather than double-registering.
        self._loaded_entry_points: set[str] = set()

    @property
    def extensions(self) -> list[Extension]:
        return list(self._extensions)

    def register(self, extension: Extension) -> None:
        self._extensions.append(extension)

    def reset(self, extensions: list[Extension] | None = None) -> None:
        """Replace the registered set (mainly for tests). Empty by default."""
        self._extensions = list(extensions) if extensions else []
        self._loaded_entry_points = set()

    def load_from_entry_points(self, group: str = ENTRY_POINT_GROUP) -> None:
        """Discover and register extensions from the ``catlico.extensions``
        entry-point group. Each entry point resolves to an object implementing
        (part of) the ``Extension`` protocol. A broken extension is logged and
        skipped rather than crashing startup.

        Idempotent: entry points already loaded (tracked by name) are skipped, so
        calling this repeatedly never double-registers. The OSS empty-group path
        is a clean no-op."""
        for ep in importlib.metadata.entry_points(group=group):
            if ep.name in self._loaded_entry_points:
                continue
            try:
                extension = ep.load()
            except Exception:  # noqa: BLE001 — a bad extension must not down the app
                logger.exception("failed to load extension entry point %s", ep.name)
                continue
            self.register(extension)
            self._loaded_entry_points.add(ep.name)
            logger.info("loaded extension %s", ep.name)

    def routers(self) -> list[APIRouter]:
        result: list[APIRouter] = []
        for ext in self._extensions:
            hook = getattr(ext, "routers", None)
            if hook is not None:
                result.extend(hook())
        return result

    def identity_providers(self) -> list[IdentityProvider]:
        result: list[IdentityProvider] = []
        for ext in self._extensions:
            hook = getattr(ext, "identity_providers", None)
            if hook is not None:
                result.extend(hook())
        return result

    def capabilities(self) -> dict[str, bool]:
        merged = dict(DEFAULT_CAPABILITIES)
        for ext in self._extensions:
            hook = getattr(ext, "capabilities", None)
            if hook is not None:
                merged.update(hook())
        return merged

    async def second_factor_challenge(self, user: User) -> Challenge | None:
        """Call each extension's ``second_factor_hook`` (sync or async) after a
        successful password check. The first non-``None`` challenge wins; with no
        extension this returns ``None`` and login proceeds to issue tokens."""
        for ext in self._extensions:
            hook = getattr(ext, "second_factor_hook", None)
            if hook is None:
                continue
            result = hook(user)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        return None

    async def case_transition_denial(
        self,
        *,
        case: Case,
        from_status: CaseStatusRef,
        to_status: CaseStatusRef,
        actor: AuthContext,
    ) -> Denial | None:
        """Ask each extension's ``case_transition_hook`` (sync or async) whether this
        actor may move this case from one status to another. The first non-``None``
        denial wins; with no extension this returns ``None`` and the transition
        proceeds — preserving the OSS contract that case status transitions are
        unrestricted (``CaseUpdate.status_id``).

        Called only when the status actually *changes*, so a no-op PATCH never
        consults a policy. Same shape as :meth:`second_factor_challenge`.
        """
        for ext in self._extensions:
            hook = getattr(ext, "case_transition_hook", None)
            if hook is None:
                continue
            result = hook(
                case=case,
                from_status=from_status,
                to_status=to_status,
                actor=actor,
            )
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                return result
        return None

    async def run_startup_hooks(self) -> None:
        """Await each loaded extension's optional ``on_startup`` hook once, in
        registration order. Called from the app lifespan AFTER catlico-api's own
        DB init/migrations, so an extension can create/query its own tables or
        warm a cache. An extension without ``on_startup`` is skipped; a hook that
        raises is logged and skipped so a broken extension can't crash the OSS
        app boot (same resilience contract as entry-point discovery). A no-op in
        OSS where no extension is loaded."""
        for ext in self._extensions:
            hook = getattr(ext, "on_startup", None)
            if hook is None:
                continue
            try:
                result = hook()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 — a bad hook must not down the app
                logger.exception(
                    "extension startup hook failed for %r", ext
                )


#: Process-wide singleton. Populated once at app module load from entry points.
registry = ExtensionRegistry()


def mount_extension_router(app: FastAPI, router: APIRouter) -> None:
    app.include_router(router, prefix=EXTENSION_ROUTER_PREFIX)


def install_extension(app: FastAPI, extension: Extension) -> None:
    """Register a single extension and mount its routers immediately. Used both
    by tests (to inject the fixture extension) and internally by
    :func:`load_and_mount_extensions`."""
    registry.register(extension)
    hook = getattr(extension, "routers", None)
    if hook is not None:
        for router in hook():
            mount_extension_router(app, router)


def load_and_mount_extensions(app: FastAPI, group: str = ENTRY_POINT_GROUP) -> None:
    """Discover extensions from entry points and mount their routers. Called once
    at app module load; a no-op in OSS where the entry-point group is empty."""
    before = set(registry.extensions)
    registry.load_from_entry_points(group)
    for ext in registry.extensions:
        if ext in before:
            continue
        hook = getattr(ext, "routers", None)
        if hook is not None:
            for router in hook():
                mount_extension_router(app, router)
