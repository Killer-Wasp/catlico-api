"""Phase 6 §6.2 — cross-replica WebSocket fan-out over Postgres LISTEN/NOTIFY.

The WS hub (`websocket_hub.WebSocketHub`) is a process-local registry: a broadcast
only reaches sockets connected to *this* replica. With replicas > 1 an event drained
on replica A would never reach a client connected to replica B.

This module is the pub/sub bus that fixes that:

- **Publish** (`publish_broadcast` / `publish_user`) runs a `pg_notify` *inside the
  drain transaction*. Postgres holds the notification until the transaction commits
  (and discards it on rollback), so it fires exactly when — and only if — the event
  is durably committed. The payload is a tiny **reference** (org id + row id + a kind
  marker), never the full envelope: NOTIFY has an ~8 KB payload cap, so we notify-then-
  fetch.

- **Listen** (`EventListener.run`) holds a *dedicated, long-lived asyncpg connection*
  — NOT one from the SQLAlchemy pool, which the ORM would recycle out from under a
  `LISTEN`. Every replica runs one. On each notification it fetches the referenced row
  on a normal pooled session, rebuilds the WS message, and feeds its *local* hub. So
  the replica that drained and every other replica each deliver to their own connected
  clients exactly once (the draining replica no longer pushes directly — the NOTIFY it
  emits comes back to its own listener).

On connection loss the listener reconnects with capped exponential backoff and runs a
**catch-up drain pass** on every (re)connect: rows that accumulated undelivered during
the outage are drained (which re-emits their NOTIFYs) so a transient LISTEN drop can't
strand pending events. A lone replica works unchanged — it publishes and listens to
itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings

logger = logging.getLogger(__name__)

#: The single Postgres NOTIFY channel every replica listens on.
EVENT_CHANNEL = "catlico_events"

_INITIAL_BACKOFF_SECONDS = 0.5
_MAX_BACKOFF_SECONDS = 30.0


# --- Publish side (runs inside the drain transaction) ---------------------------


async def _notify(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Queue a NOTIFY on the caller's transaction. Delivered on commit, dropped on
    rollback — so it rides the outbox drain's atomicity for free."""
    await session.execute(
        text("SELECT pg_notify(:chan, :payload)"),
        {"chan": EVENT_CHANNEL, "payload": json.dumps(payload)},
    )


async def publish_broadcast(
    session: AsyncSession, *, org_id: str, outbox_id: int
) -> None:
    """Announce an org-wide event. Carries only the outbox row id; listeners fetch
    the row and build the envelope (notify-then-fetch, 8 KB-safe)."""
    await _notify(
        session, {"kind": "broadcast", "org_id": org_id, "outbox_id": outbox_id}
    )


async def publish_user(
    session: AsyncSession, *, org_id: str, user_id: str, notification_id: str
) -> None:
    """Announce a targeted per-user push (the 'target marker' is `kind == "user"` +
    `user_id`). Carries only the notification row id; listeners fetch it and build
    the WS frame."""
    await _notify(
        session,
        {
            "kind": "user",
            "org_id": org_id,
            "user_id": user_id,
            "notification_id": notification_id,
        },
    )


# --- Dispatch side (runs on the listener, feeds the local hub) ------------------


async def dispatch_event(payload: dict[str, Any]) -> None:
    """Route one decoded NOTIFY payload to the local hub. Fetches the referenced row
    on a fresh pooled session, rebuilds the WS message, and delivers it locally."""
    kind = payload.get("kind")
    if kind == "broadcast":
        await _dispatch_broadcast(payload["org_id"], int(payload["outbox_id"]))
    elif kind == "user":
        await _dispatch_user(
            payload["org_id"], payload["user_id"], payload["notification_id"]
        )
    else:  # pragma: no cover - defensive
        logger.warning("event bus: unknown NOTIFY kind %r", kind)


async def _dispatch_broadcast(org_id: str, outbox_id: int) -> None:
    from app.core.db import AsyncSessionLocal
    from app.models.audit import AuditOutbox
    from app.services.outbox_events import build_event_envelope
    from app.services.websocket_hub import get_hub

    async with AsyncSessionLocal() as session:
        row = await session.get(AuditOutbox, outbox_id)
        if row is None:
            # The row was pruned between NOTIFY and fetch — nothing to broadcast.
            return
        envelope = build_event_envelope(row)
    await get_hub().broadcast(org_id, {"type": "event", "event": envelope})


async def _dispatch_user(org_id: str, user_id: str, notification_id: str) -> None:
    from app.core.db import AsyncSessionLocal
    from app.models.notification import UserNotification, UserNotificationPublic
    from app.services.websocket_hub import get_hub

    try:
        notif_uuid = uuid.UUID(notification_id)
    except (ValueError, AttributeError, TypeError):
        logger.warning("event bus: invalid notification id %r", notification_id)
        return
    async with AsyncSessionLocal() as session:
        notif = await session.get(UserNotification, notif_uuid)
        if notif is None:
            return
        message = {
            "type": "notification",
            "notification": UserNotificationPublic(
                id=notif.id,
                event_type=notif.event_type,
                title=notif.title,
                body=notif.body,
                payload=notif.payload,
                read_at=None,
                created_at=notif.created_at,
            ).model_dump(mode="json"),
        }
    await get_hub().send_to_user(org_id, user_id, message)


# --- Listener (one dedicated asyncpg connection per replica) --------------------


def _raw_asyncpg_dsn() -> str:
    """The SQLAlchemy URL uses the `postgresql+asyncpg://` driver prefix; asyncpg's
    own `connect` wants a bare `postgresql://` DSN."""
    return settings.SQLALCHEMY_DATABASE_URI.replace(
        "postgresql+asyncpg://", "postgresql://"
    )


class EventListener:
    """Owns the dedicated LISTEN connection and its reconnect lifecycle.

    `dispatch` handles one decoded payload (default: `dispatch_event`). `catch_up`,
    if given, runs once on every (re)connect — a single outbox drain pass in the app
    — so events committed during a LISTEN outage are recovered.
    """

    def __init__(
        self,
        *,
        dispatch: Callable[[dict[str, Any]], Awaitable[None]] = dispatch_event,
        catch_up: Callable[[], Awaitable[None]] | None = None,
        dsn: str | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._catch_up = catch_up
        self._dsn = dsn
        self._tasks: set[asyncio.Task] = set()
        #: Set once the LISTEN is established, cleared on connection loss. Lets a
        #: caller (or a test) wait for the bus to be live before publishing.
        self.listening = asyncio.Event()

    def _on_notify(
        self, conn: object, pid: int, channel: str, payload: str
    ) -> None:
        """asyncpg's listener callback is synchronous — decode and hand off to a
        task so the (async) hub delivery doesn't block the connection's read loop."""
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            logger.warning("event bus: undecodable NOTIFY payload %r", payload)
            return
        task = asyncio.create_task(self._safe_dispatch(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_dispatch(self, data: dict[str, Any]) -> None:
        try:
            await self._dispatch(data)
        except Exception:  # noqa: BLE001 — one bad event must not kill the listener
            logger.exception("event bus: dispatch failed for %r", data)

    async def run(self) -> None:
        """Connect, LISTEN, and stay up until cancelled. Reconnects with capped
        exponential backoff on any connection error; runs the catch-up drain on
        every successful (re)connect."""
        backoff = _INITIAL_BACKOFF_SECONDS
        while True:
            conn = None
            try:
                conn = await asyncpg.connect(self._dsn or _raw_asyncpg_dsn())
                await conn.add_listener(EVENT_CHANNEL, self._on_notify)
                logger.info("event bus: LISTEN %s established", EVENT_CHANNEL)
                self.listening.set()
                backoff = _INITIAL_BACKOFF_SECONDS  # reset after a clean connect
                if self._catch_up is not None:
                    try:
                        await self._catch_up()
                    except Exception:  # noqa: BLE001
                        logger.exception("event bus: catch-up drain failed")
                closed = asyncio.Event()
                conn.add_termination_listener(lambda _c: closed.set())
                # Park until the server drops the connection (or we're cancelled).
                await closed.wait()
                self.listening.clear()
                logger.warning("event bus: LISTEN connection lost; reconnecting")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — retry any connect/listen failure
                logger.exception(
                    "event bus: listener error; retrying in %.1fs", backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            finally:
                self.listening.clear()
                if conn is not None:
                    with suppress(Exception):
                        await conn.close(timeout=5)
