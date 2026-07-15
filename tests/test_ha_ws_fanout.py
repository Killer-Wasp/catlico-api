"""Phase 6 §6.2 — cross-process WebSocket fan-out over LISTEN/NOTIFY.

Pins the property that makes the WS hub replica-safe: an event committed by the
outbox drain in *this* process is delivered to a hub running in a *separate*
process, purely via the Postgres `catlico_events` channel. This is the multi-
replica scenario in miniature — a subscriber process with its own hub + dedicated
LISTEN connection, and a publisher process that drains an outbox row.

Marked `slow`: it spawns a real subprocess and round-trips through Postgres.
"""

import multiprocessing
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.crud.audit import _consumers, dispatch_pending_outbox
from app.models.audit import Audit, AuditOutbox
from app.services.websocket_hub import ws_broadcast_consumer

_DB_URL = os.environ["DATABASE_URL"]
_ORG_ID = "org-fanout"


def _subscriber_main(db_url: str, org_id: str, out_q, ready_q) -> None:
    """Run in a spawned child: a WS hub with one recording socket + an EventListener
    on its own LISTEN connection. Every frame the hub delivers is pushed to `out_q`;
    `ready_q` gets a token once the LISTEN is live so the parent can publish safely."""
    os.environ["DATABASE_URL"] = db_url
    import asyncio

    from starlette.websockets import WebSocketState

    from app.services.event_bus import EventListener
    from app.services.websocket_hub import get_hub

    class _RecordingWS:
        client_state = WebSocketState.CONNECTED

        async def send_json(self, message):
            out_q.put(message)

    async def _run() -> None:
        hub = get_hub()
        hub._orgs[org_id] = [_RecordingWS()]
        listener = EventListener()
        task = asyncio.create_task(listener.run())
        try:
            await asyncio.wait_for(listener.listening.wait(), timeout=30)
            ready_q.put("ready")
            # Stay alive so the LISTEN keeps receiving; parent terminates us.
            await asyncio.sleep(60)
        finally:
            task.cancel()

    asyncio.run(_run())


async def _commit_broadcast_event(org_id: str) -> None:
    """Insert an undelivered outbox row for `org_id` and drain it (with the real
    ws_broadcast_consumer registered), so the drain's commit fires the NOTIFY."""
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    _consumers.append(ws_broadcast_consumer)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            audit = Audit(
                request_id="req-fanout",
                action="create",
                object_type="case",
                object_id="1",
                actor="system",
            )
            s.add(audit)
            await s.flush()
            s.add(
                AuditOutbox(
                    audit_id=audit.id,
                    topic="audit",
                    payload={
                        "organisation_id": org_id,
                        "object_type": "case",
                        "object_id": "1",
                        "action": "create",
                        "actor": "system",
                    },
                )
            )
            await s.commit()
        # Fresh session for the drain (its commit is what delivers the NOTIFY).
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            delivered = await dispatch_pending_outbox(s)
            assert delivered == 1
    finally:
        _consumers.remove(ws_broadcast_consumer)
        await eng.dispose()


@pytest.mark.slow
async def test_notify_roundtrip_to_hub_in_another_process():
    ctx = multiprocessing.get_context("spawn")
    out_q = ctx.Queue()
    ready_q = ctx.Queue()
    proc = ctx.Process(
        target=_subscriber_main, args=(_DB_URL, _ORG_ID, out_q, ready_q)
    )
    proc.start()
    try:
        # Block until the child's LISTEN is established, else the NOTIFY is missed.
        assert ready_q.get(timeout=45) == "ready"

        await _commit_broadcast_event(_ORG_ID)

        # The child hub must have delivered exactly the broadcast frame.
        message = out_q.get(timeout=20)
        assert message["type"] == "event"
        assert message["event"]["event_type"] == "case.created"
        assert message["event"]["object"]["type"] == "case"
    finally:
        proc.terminate()
        proc.join(timeout=10)
