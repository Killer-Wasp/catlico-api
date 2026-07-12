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
    hub.disconnect("org-a", ws, "user-1")
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


# --- Per-user targeted delivery (send_to_user) ----------------------------------------


async def test_connect_registers_in_both_org_and_user_buckets():
    """connect() puts the socket in both _orgs[org] and _users[(org,user)]."""
    hub = get_hub()
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED

    await hub.connect("org-a", ws, "user-1")

    assert ws in hub._orgs["org-a"]
    assert ws in hub._users[("org-a", "user-1")]


async def test_disconnect_removes_from_both_buckets_and_cleans_empty():
    """disconnect() removes the socket from both indexes and drops empty buckets."""
    hub = get_hub()
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED

    await hub.connect("org-a", ws, "user-1")
    hub.disconnect("org-a", ws, "user-1")

    assert "org-a" not in hub._orgs
    assert ("org-a", "user-1") not in hub._users


async def test_send_to_user_delivers_only_to_target_user():
    """send_to_user reaches the target (org,user)'s sockets only."""
    hub = get_hub()
    ws_target = AsyncMock()
    ws_target.client_state = WebSocketState.CONNECTED
    ws_other_user = AsyncMock()
    ws_other_user.client_state = WebSocketState.CONNECTED

    await hub.connect("org-a", ws_target, "user-1")
    await hub.connect("org-a", ws_other_user, "user-2")

    msg = {"type": "notification", "notification": {"id": "n1"}}
    await hub.send_to_user("org-a", "user-1", msg)

    ws_target.send_json.assert_called_once_with(msg)
    ws_other_user.send_json.assert_not_called()


async def test_send_to_user_does_not_cross_org():
    """A socket for the same user id in a different org does not receive it."""
    hub = get_hub()
    ws_a = AsyncMock()
    ws_a.client_state = WebSocketState.CONNECTED
    ws_b = AsyncMock()
    ws_b.client_state = WebSocketState.CONNECTED

    await hub.connect("org-a", ws_a, "user-1")
    await hub.connect("org-b", ws_b, "user-1")

    await hub.send_to_user("org-a", "user-1", {"type": "notification"})

    ws_a.send_json.assert_called_once()
    ws_b.send_json.assert_not_called()


async def test_send_to_user_noop_when_user_absent():
    """No connections for the user → silent no-op (no raise)."""
    hub = get_hub()
    await hub.send_to_user("org-a", "ghost", {"type": "notification"})  # must not raise


async def test_send_to_user_prunes_dead_sockets():
    """A socket that raises on send is removed from the user bucket."""
    hub = get_hub()
    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    ws.send_json.side_effect = RuntimeError("connection lost")

    await hub.connect("org-a", ws, "user-1")
    await hub.send_to_user("org-a", "user-1", {"type": "notification"})

    assert ("org-a", "user-1") not in hub._users or ws not in hub._users.get(
        ("org-a", "user-1"), []
    )


# --- Route-level tests ----------------------------------------------------------------
# ponytail: WebSocket route tests skipped — starlette TestClient websocket_connect
# hangs in the current asyncpg + asyncio backend config. Hub-level tests (above)
# cover the core broadcast/disconnect logic. Add route tests when the test
# backend supports ASGI WebSocket sessions without blocking.
#
# Test plan for when backend is ready:
#   - test_ws_route_connect_succeeds: valid JWT + org → connection accepted
#   - test_ws_route_missing_token_fails: no token → 1008 close
#   - test_ws_route_bad_token_fails: invalid token → 1008 close
#   - test_ws_route_cross_org_fails: token for org-b cannot connect as org-a
#   - test_ws_route_requires_read_case: a member whose role lacks read:case
#     (expanded from the role's groups) → 1008 close; superadmin bypasses
