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
    """Global org→connections registry. Each org can have multiple connections."""

    def __init__(self) -> None:
        self._orgs: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, org_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._orgs[org_id].append(ws)
        logger.info("ws connected org=%s total=%d", org_id, len(self._orgs[org_id]))

    def disconnect(self, org_id: str, ws: WebSocket) -> None:
        try:
            self._orgs[org_id].remove(ws)
            logger.info("ws disconnected org=%s remaining=%d", org_id, len(self._orgs[org_id]))
            if not self._orgs[org_id]:
                del self._orgs[org_id]
        except (ValueError, KeyError):
            pass

    async def broadcast(self, org_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected client in `org_id`."""
        dead: list[WebSocket] = []
        for ws in self._orgs.get(org_id, []):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(org_id, ws)

    async def handle(self, org_id: str, ws: WebSocket) -> None:
        """Keep-alive loop: hold the connection open until the client disconnects."""
        await self.connect(org_id, ws)
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
            self.disconnect(org_id, ws)


# ponytail: global singleton — per-org sharding if throughput matters
_hub: WebSocketHub | None = None


def get_hub() -> WebSocketHub:
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub


async def ws_broadcast_consumer(session, row) -> None:
    """Outbox consumer: broadcast the event envelope to all connected clients
    in the event's organisation."""
    from app.services.outbox_events import build_event_envelope

    org_id = row.payload.get("organisation_id")
    if not org_id:
        return
    envelope = build_event_envelope(row)
    hub = get_hub()
    await hub.broadcast(
        org_id,
        {"type": "event", "event": envelope},
    )
