"""A1: Outbox consumer contract tests — dispatch success, failure, retry, idempotency."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import register_consumer, _consumers
from app.models.audit import Audit, AuditOutbox
from app.models.case_ import Case


# ponytail: save/restore global consumers to isolate unit tests from the
# app-wide consumers registered at startup (notify_feed, notifier_delivery, ws)
class _ConsumerGuard:
    def __enter__(self):
        self._saved = list(_consumers)
        _consumers.clear()
        return self

    def __exit__(self, *args):
        _consumers.clear()
        _consumers.extend(self._saved)


async def _a_case(session) -> Case:
    case = Case(title="test case", created_by="system")
    session.add(case)
    await session.flush()
    return case


async def _an_outbox_row(session, org_id: str = "test-org") -> AuditOutbox:
    """Create a case + audit → outbox and return the outbox row."""
    case = await _a_case(session)
    await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-test",
        details={"key": "value"},
        organisation_id=org_id,
    )
    await session.commit()
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    return result.scalars().one()


# ---------------------------------------------------------------------------
# Consumer contract
# ---------------------------------------------------------------------------


async def test_consumer_receives_session_and_outbox_row(session):
    """Registered consumer is called with (AsyncSession, AuditOutbox)."""
    row = await _an_outbox_row(session)
    seen: list[tuple[int, int]] = []

    async def _consumer(s, r):
        seen.append((r.id, r.audit_id))

    register_consumer(_consumer)
    try:
        # Reset delivered so drain picks it up.
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered == 1
        assert len(seen) == 1
        assert seen[0] == (row.id, row.audit_id)
    finally:
        _consumers.remove(_consumer)


async def test_successful_consumer_marks_delivered(session):
    """All consumers succeed → row is marked delivered."""
    row = await _an_outbox_row(session)

    async def _consumer(s, r):
        pass

    register_consumer(_consumer)
    try:
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered == 1

        # Re-fetch to confirm persistence.
        await session.refresh(row)
        assert row.delivered_at is not None
        assert row.attempts == 1
    finally:
        _consumers.remove(_consumer)


async def test_failed_consumer_leaves_undelivered(session):
    """A consumer that raises leaves the row undelivered (attempts incremented)."""
    row = await _an_outbox_row(session)

    async def _failing(s, r):
        raise RuntimeError("boom")

    register_consumer(_failing)
    try:
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered == 0

        await session.refresh(row)
        assert row.delivered_at is None
        assert row.attempts == 1
    finally:
        _consumers.remove(_failing)


async def test_attempts_increment_on_retry(session):
    """Each drain attempt increments the attempts counter."""
    row = await _an_outbox_row(session)

    async def _failing(s, r):
        raise RuntimeError("boom")

    register_consumer(_failing)
    try:
        for expected_attempts in (1, 2, 3):
            row.delivered_at = None
            session.add(row)
            await session.commit()

            delivered = await audit_crud.dispatch_pending_outbox(session)
            assert delivered == 0

            await session.refresh(row)
            assert row.attempts == expected_attempts
    finally:
        _consumers.remove(_failing)


async def test_multiple_consumers_all_succeed(session):
    """All registered consumers fire for each row."""
    row = await _an_outbox_row(session)
    calls: list[str] = []

    async def _c1(s, r):
        calls.append("c1")

    async def _c2(s, r):
        calls.append("c2")

    register_consumer(_c1)
    register_consumer(_c2)
    try:
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered == 1
        assert calls == ["c1", "c2"]

        await session.refresh(row)
        assert row.delivered_at is not None
    finally:
        _consumers.remove(_c1)
        _consumers.remove(_c2)


async def test_first_consumer_failure_stops_fan_out(session):
    """When c1 fails, c2 should still receive the row (each consumer is independent)."""
    row = await _an_outbox_row(session)
    calls: list[str] = []

    async def _c1(s, r):
        calls.append("c1")
        raise RuntimeError("c1 boom")

    async def _c2(s, r):
        calls.append("c2")

    register_consumer(_c1)
    register_consumer(_c2)
    try:
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)
        # Any consumer failure → row not delivered
        assert delivered == 0
        # Both consumers should still have been called (current impl iterates all)
        assert "c1" in calls
        assert "c2" in calls

        await session.refresh(row)
        assert row.delivered_at is None
    finally:
        _consumers.remove(_c1)
        _consumers.remove(_c2)


async def test_failing_consumer_does_not_poison_later_consumers(session, org_a):
    """A consumer that dirties the transaction (e.g. FK violation) must not break
    the next consumer or the batch commit — each consumer runs in a SAVEPOINT."""
    from app.models.notification import UserNotification

    row = await _an_outbox_row(session, org_id=org_a.id)
    seen: list[int] = []

    async def poison_consumer(s, r):
        # Simulate the historical bug: insert a row violating the org FK.
        s.add(
            UserNotification(
                organisation_id="no-such-org",
                user_id=None,
                event_type="x",
                title="x",
            )
        )
        await s.flush()

    async def good_consumer(s, r):
        seen.append(r.id)

    register_consumer(poison_consumer)
    register_consumer(good_consumer)
    try:
        row.delivered_at = None
        row.attempts = 0
        session.add(row)
        await session.commit()

        delivered = await audit_crud.dispatch_pending_outbox(session)

        assert seen, "good consumer must still run after the poison consumer"
        # The poison consumer's failure keeps the row undelivered.
        assert delivered == 0

        # And the session must still be usable — a follow-up query must not raise
        # PendingRollbackError / InFailedSQLTransaction.
        await session.execute(select(AuditOutbox))
    finally:
        _consumers.remove(poison_consumer)
        _consumers.remove(good_consumer)


async def test_no_consumers_still_marks_delivered(session):
    """With zero registered consumers, rows are delivered (existing v1 behavior)."""
    row = await _an_outbox_row(session)

    row.delivered_at = None
    row.attempts = 0
    session.add(row)
    await session.commit()

    delivered = await audit_crud.dispatch_pending_outbox(session)
    assert delivered == 1

    await session.refresh(row)
    assert row.delivered_at is not None


# ---------------------------------------------------------------------------
# Event envelope (outbox_events service)
# ---------------------------------------------------------------------------


async def test_build_event_envelope_from_outbox_row(session):
    """Event envelope normalizes audit actions into plugin event types."""
    from app.services.outbox_events import build_event_envelope

    row = await _an_outbox_row(session)

    envelope = build_event_envelope(row)
    assert envelope["event_id"] == f"audit:{row.audit_id}"
    assert envelope["event_type"] == "case.created"
    assert envelope["actor"] == "user-test"
    assert envelope["object"]["type"] == "case"
    assert envelope["object"]["id"] == str(row.audit_id)
    # Area 8 enrichment: record_audit stamps the entity's title onto the envelope.
    assert envelope["object"]["title"]
    assert envelope["details"] == {"key": "value"}
    assert envelope["created_at"]
