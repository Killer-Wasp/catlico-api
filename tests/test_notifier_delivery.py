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


async def test_kafka_is_not_delivered(org_a, session):
    """Kafka notifier type is an explicit non-delivering type (server-side no-op)."""
    case = Case(title="test", created_by="system", organisation_id=org_a.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org_a.id,
    )
    await session.commit()

    kafka_notifier = Notifier(
        organisation_id=org_a.id, type=NotifierType.kafka,
        target="kafka-topic", enabled=True,
        created_by="system",
    )
    session.add(kafka_notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org_a.id, name="kafka rule", event="case.created",
        enabled=True, notifier_ids=[str(kafka_notifier.id)],
        created_by="system",
    )
    session.add(rule)
    await session.commit()

    async def _run():
        await audit_crud.dispatch_pending_outbox(session)
        from app.models.notification import NotifierDelivery
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == kafka_notifier.id)
        )
        deliveries = result.scalars().all()
        for d in deliveries:
            assert d.status != "sent"

    await _with_consumer(_run)


# --- Email notifier sender --------------------------------------------------


def _mk_email_notifier(org_id, recipients):
    return Notifier(
        organisation_id=org_id,
        type=NotifierType.email,
        target="Ops inbox",
        config={"recipients": recipients} if recipients is not None else {},
        enabled=True,
        created_by="system",
    )


async def test_send_email_calls_smtp_helper_with_recipients_subject_body(org_a):
    """_send_email hands the configured recipients + a summary to the SMTP helper."""
    from app.services import notifier_delivery

    notifier = _mk_email_notifier(org_a.id, ["a@example.com", "b@example.com"])
    payload = {
        "event_type": "case.created",
        "actor": "alice",
        "object": {"type": "case", "id": "42"},
        "context": {"type": "case", "id": "42"},
        "created_at": "2026-07-14T00:00:00Z",
    }

    mock_send = AsyncMock()
    with patch.object(notifier_delivery, "send_email", mock_send):
        await notifier_delivery._send_email(notifier, payload)

    mock_send.assert_awaited_once()
    (to, subject, body) = mock_send.await_args.args
    assert to == ["a@example.com", "b@example.com"]
    assert subject == "[catlico] case.created by alice"
    assert "case.created" in body
    assert "alice" in body
    assert "case 42" in body
    assert "2026-07-14T00:00:00Z" in body


async def test_send_email_empty_recipients_raises(org_a):
    """Missing/empty recipients raise (consumer records the delivery failed)."""
    from app.services import notifier_delivery

    notifier = _mk_email_notifier(org_a.id, [])
    mock_send = AsyncMock()
    with patch.object(notifier_delivery, "send_email", mock_send):
        with pytest.raises(ValueError):
            await notifier_delivery._send_email(notifier, {"event_type": "e"})
    mock_send.assert_not_awaited()


async def test_send_email_unconfigured_smtp_raises(org_a, monkeypatch):
    """With recipients set but SMTP unconfigured, the SMTP helper raises."""
    from app.core.configs import settings
    from app.services import notifier_delivery
    from app.services.smtp import SMTPNotConfiguredError

    monkeypatch.setattr(settings, "SMTP_HOST", None)
    notifier = _mk_email_notifier(org_a.id, ["a@example.com"])
    with pytest.raises(SMTPNotConfiguredError):
        await notifier_delivery._send_email(
            notifier, {"event_type": "e", "actor": "x"}
        )


async def _setup_email(org, session, recipients):
    """Create a case, audit row, rule, and email notifier for testing."""
    case = Case(title="test", created_by="system", organisation_id=org.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    notifier = _mk_email_notifier(org.id, recipients)
    session.add(notifier)
    await session.flush()

    rule = NotificationRule(
        organisation_id=org.id, name="email rule", event="case.created",
        enabled=True, notifier_ids=[str(notifier.id)],
        created_by="system",
    )
    session.add(rule)
    await session.commit()
    return notifier


async def test_email_delivery_success_records_sent(org_a, session, monkeypatch):
    """A successful email send records status=sent."""
    from app.core.configs import settings
    from app.services import notifier_delivery
    from app.models.notification import NotifierDelivery

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    notifier = await _setup_email(org_a, session, ["ops@example.com"])

    async def _run():
        mock_send = AsyncMock()
        with patch.object(notifier_delivery, "send_email", mock_send):
            await audit_crud.dispatch_pending_outbox(session)
        mock_send.assert_awaited_once()
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == notifier.id)
        )
        delivery = result.scalars().one()
        assert delivery.status == "sent"

    await _with_consumer(_run)


async def test_email_unconfigured_smtp_records_failed(org_a, session, monkeypatch):
    """SMTP unset => delivery recorded failed with 'SMTP not configured'."""
    from app.core.configs import settings
    from app.services import notifier_delivery  # noqa: F401 - registers real sender
    from app.models.notification import NotifierDelivery

    monkeypatch.setattr(settings, "SMTP_HOST", None)
    notifier = await _setup_email(org_a, session, ["ops@example.com"])

    async def _run():
        await audit_crud.dispatch_pending_outbox(session)
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == notifier.id)
        )
        delivery = result.scalars().one()
        assert delivery.status == "failed"
        assert "SMTP not configured" in (delivery.last_error or "")

    await _with_consumer(_run)


async def test_email_empty_recipients_records_failed(org_a, session, monkeypatch):
    """No recipients => delivery recorded failed with a clear reason."""
    from app.core.configs import settings
    from app.models.notification import NotifierDelivery

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    notifier = await _setup_email(org_a, session, [])

    async def _run():
        await audit_crud.dispatch_pending_outbox(session)
        result = await session.execute(
            select(NotifierDelivery).where(NotifierDelivery.notifier_id == notifier.id)
        )
        delivery = result.scalars().one()
        assert delivery.status == "failed"
        assert "recipients" in (delivery.last_error or "")

    await _with_consumer(_run)
