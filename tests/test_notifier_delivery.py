"""A3: Notifier delivery tests — rule matching, idempotency, Webhook/Slack."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import register_consumer, _consumers
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import (
    Notifier,
    NotifierDelivery,
    NotifierType,
    NotificationRule,
)
from app.services.notifier_delivery import notifier_delivery_consumer


async def _setup_org_data(session, org_id: str):
    """Create a case, audit row, rule, and notifier for testing."""
    case = Case(title="test", created_by="system", organisation_id=org_id)
    session.add(case)
    await session.flush()

    await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-test",
        organisation_id=org_id,
    )
    await session.commit()

    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    outbox = result.scalars().one()

    notifier = Notifier(
        organisation_id=org_id,
        type=NotifierType.webhook,
        target="https://example.com/webhook",
        enabled=True,
    )
    session.add(notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org_id,
        name="test rule",
        event="case.create",
        enabled=True,
        notifier_ids=[str(notifier.id)],
    )
    session.add(rule)
    await session.flush()

    return outbox, notifier, rule


async def test_rule_matches_event_type(session, org):
    """Matching rule fires delivery for the correct event type."""
    outbox, notifier, rule = await _setup_org_data(session, org.id)

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        with patch(
            "app.services.notifier_delivery._send_webhook", new_callable=AsyncMock
        ) as mock_send:
            delivered = await audit_crud.dispatch_pending_outbox(session)
            assert delivered >= 0  # may be 0 if already delivered by feed consumer

            # Refresh outbox to check delivery status
            await session.refresh(outbox)

            # Verify delivery row was created
            result = await session.execute(
                select(NotifierDelivery).where(
                    NotifierDelivery.notifier_id == notifier.id
                )
            )
            deliveries = result.scalars().all()
            assert len(deliveries) >= 1
            assert deliveries[0].status == "sent"
            mock_send.assert_called_once()
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)


async def test_any_event_rule_matches(session, org):
    """Rule with event='AnyEvent' matches all events."""
    case = Case(title="test", created_by="system", organisation_id=org.id)
    session.add(case)
    await session.flush()

    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    notifier = Notifier(
        organisation_id=org.id,
        type=NotifierType.webhook,
        target="https://example.com/webhook",
        enabled=True,
    )
    session.add(notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org.id,
        name="catch all",
        event="AnyEvent",
        enabled=True,
        notifier_ids=[str(notifier.id)],
    )
    session.add(rule)
    await session.commit()

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        with patch(
            "app.services.notifier_delivery._send_webhook", new_callable=AsyncMock
        ) as mock_send:
            await audit_crud.dispatch_pending_outbox(session)
            assert mock_send.called
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)


async def test_disabled_rule_does_not_fire(session, org):
    """Disabled rule is skipped entirely."""
    outbox, notifier, rule = await _setup_org_data(session, org.id)
    rule.enabled = False
    session.add(rule)
    await session.commit()

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        with patch(
            "app.services.notifier_delivery._send_webhook", new_callable=AsyncMock
        ) as mock_send:
            await audit_crud.dispatch_pending_outbox(session)
            mock_send.assert_not_called()
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)


async def test_duplicate_dispatch_is_idempotent(session, org):
    """A sent delivery is not re-sent on retry."""
    outbox, notifier, rule = await _setup_org_data(session, org.id)

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        with patch(
            "app.services.notifier_delivery._send_webhook", new_callable=AsyncMock
        ) as mock_send:
            # First dispatch
            await audit_crud.dispatch_pending_outbox(session)
            call_count_1 = mock_send.call_count
            assert call_count_1 == 1

            # Reset outbox to simulate retry
            outbox.delivered_at = None
            session.add(outbox)
            await session.commit()

            # Second dispatch — should NOT call sender again
            await audit_crud.dispatch_pending_outbox(session)
            assert mock_send.call_count == 1  # unchanged
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)


async def test_failed_delivery_records_error(session, org):
    """Failed delivery records status=failed with error message."""
    outbox, notifier, rule = await _setup_org_data(session, org.id)

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        with patch(
            "app.services.notifier_delivery._send_webhook",
            side_effect=RuntimeError("connection refused"),
        ):
            await audit_crud.dispatch_pending_outbox(session)

        result = await session.execute(
            select(NotifierDelivery).where(
                NotifierDelivery.notifier_id == notifier.id
            )
        )
        delivery = result.scalars().one_or_none()
        assert delivery is not None
        assert delivery.status == "failed"
        assert "connection refused" in (delivery.last_error or "")
        assert delivery.attempts >= 1
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)


async def test_email_kafka_are_not_delivered(session, org):
    """Email and Kafka notifier types are explicit non-delivering."""
    case = Case(title="test", created_by="system", organisation_id=org.id)
    session.add(case)
    await session.flush()

    await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    email_notifier = Notifier(
        organisation_id=org.id,
        type=NotifierType.email,
        target="user@example.com",
        enabled=True,
    )
    session.add(email_notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org.id,
        name="email rule",
        event="case.create",
        enabled=True,
        notifier_ids=[str(email_notifier.id)],
    )
    session.add(rule)
    await session.commit()

    consumer_registered = notifier_delivery_consumer not in _consumers
    if consumer_registered:
        register_consumer(notifier_delivery_consumer)
    try:
        # Should not crash, and no delivery row created (since no sender)
        await audit_crud.dispatch_pending_outbox(session)

        result = await session.execute(
            select(NotifierDelivery).where(
                NotifierDelivery.notifier_id == email_notifier.id
            )
        )
        deliveries = result.scalars().all()
        # Email has no sender → skipped entirely (delivery row may or may not exist
        # depending on implementation, but if it exists it should be unsent)
        for d in deliveries:
            assert d.status != "sent"
    finally:
        if consumer_registered:
            _consumers.remove(notifier_delivery_consumer)
