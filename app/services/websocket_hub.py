"""A4: In-memory WebSocket connection hub for live event broadcast.

Organisation-scoped: each connection subscribes to one organisation's events.
The hub is a global singleton — ponytail: replace with a proper pub/sub when
the connection count outgrows a single process.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

logger = logging.getLogger(__name__)


class WebSocketHub:
    """Global org→connections registry. Each org can have multiple connections.

    A connection is registered under two indexes: its org (for org-wide
    broadcast) and its (org, user) pair (for targeted per-user push). The same
    socket lives in both buckets and is removed from both on disconnect.
    """

    def __init__(self) -> None:
        self._orgs: dict[str, list[WebSocket]] = defaultdict(list)
        self._users: dict[tuple[str, str], list[WebSocket]] = defaultdict(list)

    async def connect(self, org_id: str, ws: WebSocket, user_id: str) -> None:
        await ws.accept()
        self._orgs[org_id].append(ws)
        self._users[(org_id, user_id)].append(ws)
        logger.info(
            "ws connected org=%s user=%s total=%d",
            org_id,
            user_id,
            len(self._orgs[org_id]),
        )

    def disconnect(self, org_id: str, ws: WebSocket, user_id: str) -> None:
        try:
            self._orgs[org_id].remove(ws)
            logger.info("ws disconnected org=%s remaining=%d", org_id, len(self._orgs[org_id]))
            if not self._orgs[org_id]:
                del self._orgs[org_id]
        except (ValueError, KeyError):
            pass
        try:
            self._users[(org_id, user_id)].remove(ws)
            if not self._users[(org_id, user_id)]:
                del self._users[(org_id, user_id)]
        except (ValueError, KeyError):
            pass

    async def broadcast(self, org_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected client in `org_id`."""
        dead: list[WebSocket] = []
        # Iterate a snapshot: a concurrent disconnect of a sibling socket during
        # an `await send_json` would otherwise mutate the live list mid-loop and
        # skip a still-connected client.
        for ws in list(self._orgs.get(org_id, [])):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            # Prune from the org bucket; the socket's user bucket is cleaned on
            # its own disconnect path. We don't know the user_id here, so drop
            # from the org index only — same effect for broadcast liveness.
            try:
                self._orgs[org_id].remove(ws)
                if not self._orgs[org_id]:
                    del self._orgs[org_id]
            except (ValueError, KeyError):
                pass

    async def send_to_user(
        self, org_id: str, user_id: str, message: dict[str, Any]
    ) -> None:
        """Send a JSON message to every connection for `(org_id, user_id)`.

        No-op when the user has no live connections. Dead sockets are pruned,
        mirroring `broadcast`.
        """
        dead: list[WebSocket] = []
        # Snapshot (see broadcast): guards against a sibling-tab disconnect
        # mutating the bucket during an `await send_json`.
        for ws in list(self._users.get((org_id, user_id), [])):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self._users[(org_id, user_id)].remove(ws)
                if not self._users[(org_id, user_id)]:
                    del self._users[(org_id, user_id)]
            except (ValueError, KeyError):
                pass

    async def handle(self, org_id: str, ws: WebSocket, user_id: str) -> None:
        """Keep-alive loop: hold the connection open until the client disconnects."""
        await self.connect(org_id, ws, user_id)
        try:
            while ws.client_state == WebSocketState.CONNECTED:
                # ponytail: simple ping/pong keepalive; add heartbeat config if needed
                try:
                    data = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                    if data == "ping":
                        await ws.send_text("pong")
                except asyncio.TimeoutError:
                    await ws.send_text("ping")
                except WebSocketDisconnect:
                    break
                except Exception:
                    break
        finally:
            self.disconnect(org_id, ws, user_id)


# ponytail: global singleton — per-org sharding if throughput matters
_hub: WebSocketHub | None = None


def get_hub() -> WebSocketHub:
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub


async def ws_broadcast_consumer(session, row) -> None:
    """Outbox consumer: announce the event to *every* replica's hub over the
    Postgres LISTEN/NOTIFY bus (§6.2), rather than feeding only this process's
    local hub. The NOTIFY carries just the outbox row id and fires on the drain's
    commit; each replica's `EventListener` fetches the row, builds the envelope,
    and broadcasts to its own connected clients (this replica included)."""
    from app.services.event_bus import publish_broadcast

    org_id = row.payload.get("organisation_id")
    if not org_id:
        return
    await publish_broadcast(session, org_id=org_id, outbox_id=row.id)
