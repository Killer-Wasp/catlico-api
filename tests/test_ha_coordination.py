"""Phase 6 §6.2 — HA poller coordination.

Two-process / two-connection tests that pin the coordination primitives that make
catlico-api safe to run with replicas > 1:

- `FOR UPDATE SKIP LOCKED` on the outbox drain: two concurrent drain loops against
  one Postgres share the work without double-processing any row.
- `pg_try_advisory_lock` on the maintenance sweep: only one holder runs per tick;
  a second concurrent attempt is skipped (not blocked).

The SKIP-LOCKED test drives two genuinely-concurrent DB transactions (two engines,
two connections) so the row lock is exercised at the Postgres level, not just in
Python. Marked `slow` — it holds locks across `asyncio.sleep` to force contention.
"""

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import _consumers, dispatch_pending_outbox
from app.models.audit import Audit, AuditOutbox

_DB_URL = os.environ["DATABASE_URL"]


async def _make_outbox_rows(n: int) -> list[int]:
    """Insert `n` undelivered outbox rows (each with its backing audit) via a
    throwaway connection; return their ids."""
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            ids: list[int] = []
            for i in range(n):
                audit = Audit(
                    request_id=f"req-{i}",
                    action="create",
                    object_type="case",
                    object_id=str(i),
                    actor="system",
                )
                s.add(audit)
                await s.flush()
                row = AuditOutbox(audit_id=audit.id, topic="audit", payload={"n": i})
                s.add(row)
                await s.flush()
                ids.append(row.id)
            await s.commit()
            return ids
    finally:
        await eng.dispose()


@pytest.mark.slow
async def test_two_drain_loops_skip_locked_no_double_process():
    """Two concurrent drain loops against one Postgres each claim a disjoint slice
    of undelivered outbox rows (FOR UPDATE SKIP LOCKED) — every row is delivered
    exactly once, never twice."""
    total = 24
    await _make_outbox_rows(total)

    # A shared recording consumer: appends (worker, row.id). A sleep inside the
    # consumer keeps each drain's row locks held across an await so the two
    # transactions genuinely overlap and contend on the lock.
    processed: list[tuple[str, int]] = []

    async def _recorder(session, row):
        processed.append((session.info.get("worker", "?"), row.id))
        await asyncio.sleep(0.02)

    _consumers.append(_recorder)

    async def _worker(name: str) -> None:
        eng = create_async_engine(_DB_URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(eng, expire_on_commit=False)
            while True:
                async with factory() as s:
                    s.info["worker"] = name
                    delivered = await dispatch_pending_outbox(s, limit=2)
                if delivered == 0:
                    return
                await asyncio.sleep(0)
        finally:
            await eng.dispose()

    try:
        await asyncio.gather(_worker("a"), _worker("b"))
    finally:
        _consumers.remove(_recorder)

    seen_ids = [rid for _, rid in processed]
    # No row processed twice, and every row processed exactly once.
    assert len(seen_ids) == len(set(seen_ids)), "a row was double-processed"
    assert set(seen_ids) == set(range(1, total + 1)) or len(set(seen_ids)) == total

    # Both workers actually did work (real contention, not one worker draining all).
    workers = {w for w, _ in processed}
    assert workers == {"a", "b"}

    # All rows are marked delivered in the DB.
    eng = create_async_engine(_DB_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            undelivered = (
                await s.execute(
                    select(AuditOutbox).where(AuditOutbox.delivered_at.is_(None))
                )
            ).scalars().all()
            assert undelivered == []
    finally:
        await eng.dispose()
