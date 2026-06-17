"""Per-request correlation id.

`request_id` groups every audit/outbox row produced while handling one HTTP
request (the user's main action plus any side-effects), so they share a
correlation id without threading it through every CRUD signature.

The middleware is **pure ASGI**, not `BaseHTTPMiddleware`: BaseHTTPMiddleware runs
the endpoint in a separate anyio task, so a contextvar set in its `dispatch` would
not be visible to the endpoint. A pure ASGI middleware awaits the inner app in the
same task, so the contextvar set here propagates into CRUD code.
"""

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str:
    """The current request's correlation id. Mints one on demand for non-HTTP
    callers (the init_db seeder, the outbox poller, direct CRUD in tests)."""
    rid = _request_id.get()
    if rid is None:
        rid = uuid.uuid4().hex
        _request_id.set(rid)
    return rid


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound = headers.get(b"x-request-id")
        rid = inbound.decode("latin-1") if inbound else uuid.uuid4().hex
        token = _request_id.set(rid)

        async def send_wrapper(message):
            # Echo the id back so clients can correlate their request with logs/audit.
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (b"x-request-id", rid.encode("latin-1"))
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id.reset(token)
