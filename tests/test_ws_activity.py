"""A4: WebSocket activity stream tests — connect, broadcast, cross-org isolation."""

from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from app.services.websocket_hub import get_hub


@pytest.fixture(autouse=True)
async def _clear_hub():
    """Reset the WS hub between tests to avoid cross-test connection leaks."""
    import app.services.websocket_hub as ws_mod

    ws_mod._hub = None
    yield
    hub = get_hub()
    for org_conns in list(hub._orgs.values()):
        for ws in list(org_conns):
            try:
                await ws.close()
            except Exception:
                pass
    ws_mod._hub = None


async def test_hub_broadcast_to_org():
    """Broadcast to an org sends to connected clients in that org only."""
    hub = get_hub()
    ws1 = AsyncMock()
    ws1.client_state = WebSocketState.CONNECTED
    hub._orgs["org-a"] = [ws1]

    await hub.broadcast("org-a", {"type": "event", "event": {"key": "val"}})
    ws1.send_json.assert_called_once_with({"type": "event", "event": {"key": "val"}})


async def test_hub_does_not_broadcast_to_other_org():
    """Broadcast to org-a does not send to org-b connections."""
    hub = get_hub()
    ws_a = AsyncMock()
    ws_a.client_state = WebSocketState.CONNECTED
    ws_b = AsyncMock()
    ws_b.client_state = WebSocketState.CONNECTED
    hub._orgs["org-a"] = [ws_a]
    hub._orgs["org-b"] = [ws_b]

    await hub.broadcast("org-a", {"type": "event", "event": {}})
    ws_a.send_json.assert_called()
    ws_b.send_json.assert_not_called()


async def test_hub_disconnect_cleanup():
    """Disconnected clients are removed from the registry."""
    hub = get_hub()
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    hub._orgs["org-a"] = [ws]
    hub.disconnect("org-a", ws)
    assert "org-a" not in hub._orgs


async def test_broadcast_dead_connections_cleaned():
    """Connections that fail during broadcast are cleaned up."""
    hub = get_hub()
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.send_json.side_effect = RuntimeError("connection lost")
    hub._orgs["org-a"] = [ws]
    await hub.broadcast("org-a", {"type": "event", "event": {}})
    # Dead connection should be removed
    assert "org-a" not in hub._orgs or len(hub._orgs.get("org-a", [])) == 0
