"""A3: Notifier delivery tests — rule matching, idempotency, Webhook/Slack."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import register_consumer, _consumers
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import (
    Notifier,
    NotifierType,
    NotificationRule,
)
from app.services.notifier_delivery import notifier_delivery_consumer


async def _setup(org, session):
    """Create a case, audit row, rule, and notifier for testing."""
    case = Case(title="test", created_by="system", organisation_id=org.id)
    session.add(case)
    await session.flush()

    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    result = await session.execute(select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1))
    outbox = result.scalars().one()

    notifier = Notifier(
        organisation_id=org.id, type=NotifierType.webhook,
        target="https://example.com/webhook", enabled=True,
        created_by="system",
    )
    session.add(notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org.id, name="test rule", event="case.created",
        enabled=True, notifier_ids=[str(notifier.id)],
        created_by="system",
    )
    session.add(rule)
    await session.flush()

    return outbox, notifier, rule


async def _with_consumer(test_fn):
    """Save/restore global consumers, register notifier consumer, call fn."""
    saved = list(_consumers)
    _consumers.clear()
    register_consumer(notifier_delivery_consumer)
    try:
        await test_fn()
    finally:
        _consumers.clear()
        _consumers.extend(saved)


async def test_rule_matches_event_type(org_a, session):
    """Matching rule fires delivery for the correct event type."""
    outbox, notifier, rule = await _setup(org_a, session)

    async def _run():
        from app.services.notifier_delivery import _SENDERS
        from app.models.notification import NotifierType

        mock_send = AsyncMock()
        with patch.dict(_SENDERS, {NotifierType.webhook: mock_send}):
            delivered = await audit_crud.dispatch_pending_outbox(session)
            assert delivered >= 0
            mock_send.assert_called_once()

    await _with_consumer(_run)


async def test_any_event_rule_matches(org_a, session):
    """Rule with event='AnyEvent' matches all events."""
    case = Case(title="test", created_by="system", organisation_id=org_a.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="update", obj=case, context=case, actor="user-test",
        organisation_id=org_a.id,
    )
    await session.commit()

    notifier = Notifier(
        organisation_id=org_a.id, type=NotifierType.webhook,
        target="https://example.com/webhook", enabled=True,
        created_by="system",
    )
    session.add(notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org_a.id, name="catch all", event="AnyEvent",
        enabled=True, notifier_ids=[str(notifier.id)],
        created_by="system",
    )
    session.add(rule)
    await session.commit()

    async def _run():
        from app.services.notifier_delivery import _SENDERS
        from app.models.notification import NotifierType

        mock_send = AsyncMock()
        with patch.dict(_SENDERS, {NotifierType.webhook: mock_send}):
            await audit_crud.dispatch_pending_outbox(session)
            assert mock_send.called

    await _with_consumer(_run)


async def test_disabled_rule_does_not_fire(org_a, session):
    """Disabled rule is skipped entirely."""
    outbox, notifier, rule = await _setup(org_a, session)
    rule.enabled = False
    session.add(rule)
    await session.commit()

    async def _run():
        from app.services.notifier_delivery import _SENDERS
        from app.models.notification import NotifierType

        mock_send = AsyncMock()
        with patch.dict(_SENDERS, {NotifierType.webhook: mock_send}):
            await audit_crud.dispatch_pending_outbox(session)
            mock_send.assert_not_called()

    await _with_consumer(_run)


async def test_duplicate_dispatch_is_idempotent(org_a, session):
    """A sent delivery is not re-sent on retry."""
    outbox, notifier, rule = await _setup(org_a, session)

    async def _run():
        from app.services.notifier_delivery import _SENDERS
        from app.models.notification import NotifierType

        mock_send = AsyncMock()
        with patch.dict(_SENDERS, {NotifierType.webhook: mock_send}):
            await audit_crud.dispatch_pending_outbox(session)
            call_count_1 = mock_send.call_count
            assert call_count_1 == 1

            outbox.delivered_at = None
            session.add(outbox)
            await session.commit()

            await audit_crud.dispatch_pending_outbox(session)
            assert mock_send.call_count == 1

    await _with_consumer(_run)


async def test_failed_delivery_records_error(org_a, session):
    """Failed delivery records status=failed with error message."""
    outbox, notifier, rule = await _setup(org_a, session)

    async def _run():
        from app.services.notifier_delivery import _SENDERS
        from app.models.notification import NotifierType

        mock_send = AsyncMock(side_effect=RuntimeError("connection refused"))
        with patch.dict(_SENDERS, {NotifierType.webhook: mock_send}):
            await audit_crud.dispatch_pending_outbox(session)

        from app.models.notification import NotifierDelivery
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == notifier.id)
        )
        delivery = result.scalars().one_or_none()
        assert delivery is not None
        assert delivery.status == "failed"
        assert "connection refused" in (delivery.last_error or "")
        assert delivery.attempts >= 1

    await _with_consumer(_run)


async def test_email_kafka_are_not_delivered(org_a, session):
    """Email and Kafka notifier types are explicit non-delivering."""
    case = Case(title="test", created_by="system", organisation_id=org_a.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org_a.id,
    )
    await session.commit()

    email_notifier = Notifier(
        organisation_id=org_a.id, type=NotifierType.email,
        target="user@example.com", enabled=True,
        created_by="system",
    )
    session.add(email_notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org_a.id, name="email rule", event="case.created",
        enabled=True, notifier_ids=[str(email_notifier.id)],
        created_by="system",
    )
    session.add(rule)
    await session.commit()

    async def _run():
        await audit_crud.dispatch_pending_outbox(session)
        from app.models.notification import NotifierDelivery
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == email_notifier.id)
        )
        deliveries = result.scalars().all()
        for d in deliveries:
            assert d.status != "sent"

    await _with_consumer(_run)
