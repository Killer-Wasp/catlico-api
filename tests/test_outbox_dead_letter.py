"""Outbox dead-lettering, retention pruning, SET NULL FK flip, retry idempotency.

Covers plans/phase-2 §2.4: a row that keeps failing gets dead-lettered and stops
retrying; the maintenance sweep prunes old delivered/dead outbox + read
notifications + old deliveries; pruning an outbox row leaves its child
notification/delivery rows intact (outbox_id SET NULL); a re-drained event never
duplicates a user_notification.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text

from app.core.configs import settings
from app.crud import audit as audit_crud
from app.crud import notification as notif_crud
from app.crud import overview as overview_crud
from app.crud.audit import _consumers
from app.models.audit import Audit, AuditOutbox
from app.models.notification import (
    Notifier,
    NotifierDelivery,
    NotifierType,
    UserNotification,
    UserNotificationRead,
)
from app.services import outbox_maintenance
from app.services.outbox_events import notify_feed_consumer

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


async def _audit_outbox(
    session,
    *,
    org_id=None,
    action="create",
    object_type="case",
    object_id="1",
    **outbox_kwargs,
) -> AuditOutbox:
    audit = Audit(
        request_id="r",
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor="system",
    )
    session.add(audit)
    await session.flush()
    payload = {
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "actor": "system",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if org_id:
        payload["organisation_id"] = org_id
    ob = AuditOutbox(audit_id=audit.id, topic="audit", payload=payload, **outbox_kwargs)
    session.add(ob)
    await session.flush()
    return ob


# --- Migration: FK ondelete flipped to SET NULL ------------------------------


async def test_outbox_child_fks_are_set_null(session):
    """The FK-flip migration must leave both child FKs as ON DELETE SET NULL
    (confdeltype 'n'), not CASCADE ('c') — so pruning outbox rows can't cascade
    away users' notification history or the delivery ledger."""
    rows = (
        await session.execute(
            text(
                "SELECT conname, confdeltype FROM pg_constraint "
                "WHERE contype = 'f' AND conname IN "
                "('fk_user_notification_outbox_id', 'notifier_delivery_outbox_id_fkey')"
            )
        )
    ).all()
    delrule = {
        name: (rule.decode() if isinstance(rule, bytes) else rule)
        for name, rule in rows
    }
    assert delrule.get("fk_user_notification_outbox_id") == "n"  # 'n' = SET NULL
    assert delrule.get("notifier_delivery_outbox_id_fkey") == "n"


async def test_pruning_outbox_preserves_child_notification(session, org_a):
    """Deleting an outbox row leaves its child user_notification intact with
    outbox_id nulled — the SET NULL behaviour the migration establishes."""
    ob = await _audit_outbox(session, org_id=org_a.id)
    notif = await notif_crud.create_notification(
        session,
        organisation_id=org_a.id,
        user_id=None,
        event_type="case.created",
        title="Case created",
        outbox_id=ob.id,
    )
    await session.commit()

    await session.execute(delete(AuditOutbox).where(AuditOutbox.id == ob.id))
    await session.commit()

    survivor = await session.get(UserNotification, notif.id)
    assert survivor is not None
    await session.refresh(survivor)  # FK SET NULL happened DB-side; reload it
    assert survivor.outbox_id is None


async def test_pruning_outbox_preserves_notifier_delivery(session, org_a):
    """Same guarantee for the delivery ledger: its outbox_id nulls, row survives."""
    ob = await _audit_outbox(session, org_id=org_a.id)
    notifier = Notifier(
        organisation_id=org_a.id, type=NotifierType.webhook, target="x", created_by="t"
    )
    session.add(notifier)
    await session.flush()
    delivery = NotifierDelivery(outbox_id=ob.id, notifier_id=notifier.id, status="sent")
    session.add(delivery)
    await session.commit()

    await session.execute(delete(AuditOutbox).where(AuditOutbox.id == ob.id))
    await session.commit()

    survivor = await session.get(NotifierDelivery, delivery.id)
    assert survivor is not None
    await session.refresh(survivor)  # FK SET NULL happened DB-side; reload it
    assert survivor.outbox_id is None


# --- Dead-lettering ----------------------------------------------------------


async def test_dead_letter_after_max_attempts(session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_OUTBOX_ATTEMPTS", 3)
    ob = await _audit_outbox(session, org_id="org-x")
    await session.commit()

    async def failing(s, row):
        raise RuntimeError("boom")

    saved = list(_consumers)
    _consumers.clear()
    try:
        audit_crud.register_consumer(failing)
        for _ in range(3):
            await audit_crud.dispatch_pending_outbox(session)
    finally:
        _consumers.clear()
        _consumers.extend(saved)

    row = (await session.execute(select(AuditOutbox))).scalars().one()
    assert row.attempts == 3
    assert row.dead_lettered_at is not None
    assert row.delivered_at is None

    # Dead-lettered rows are excluded from the drain and never retried again.
    processed = await audit_crud.dispatch_pending_outbox(session)
    assert processed == 0
    await session.refresh(row)
    assert row.attempts == 3


async def test_dead_letter_not_reached_below_cap(session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_OUTBOX_ATTEMPTS", 10)
    await _audit_outbox(session, org_id="org-x")
    await session.commit()

    async def failing(s, row):
        raise RuntimeError("boom")

    saved = list(_consumers)
    _consumers.clear()
    try:
        audit_crud.register_consumer(failing)
        await audit_crud.dispatch_pending_outbox(session)
    finally:
        _consumers.clear()
        _consumers.extend(saved)

    row = (await session.execute(select(AuditOutbox))).scalars().one()
    assert row.attempts == 1
    assert row.dead_lettered_at is None  # still retryable


# --- Retry idempotency (natural-key dedup) -----------------------------------


async def test_redrain_creates_no_duplicate_notification(session, org_a):
    """Re-running the feed consumer for the same outbox row is a no-op, not a
    duplicate — the (outbox_id, user_id) partial unique index + on_conflict."""
    ob = await _audit_outbox(session, org_id=org_a.id)

    await notify_feed_consumer(session, ob)
    await notify_feed_consumer(session, ob)  # re-drain after a partial failure

    count = await session.scalar(
        select(func.count())
        .select_from(UserNotification)
        .where(
            UserNotification.outbox_id == ob.id,
            UserNotification.user_id.is_(None),
        )
    )
    assert count == 1


# --- Retention pruning -------------------------------------------------------


async def test_prune_delivered_outbox(session, org_a):
    old = await _audit_outbox(
        session, org_id=org_a.id, delivered_at=NOW - timedelta(days=40)
    )
    recent = await _audit_outbox(
        session, org_id=org_a.id, delivered_at=NOW - timedelta(days=5)
    )
    undelivered = await _audit_outbox(session, org_id=org_a.id)
    # A child notification on the old row must SURVIVE the outbox prune.
    notif = await notif_crud.create_notification(
        session,
        organisation_id=org_a.id,
        user_id=None,
        event_type="case.created",
        title="t",
        outbox_id=old.id,
    )
    await session.commit()

    pruned = await outbox_maintenance.prune_delivered_outbox(session, NOW)
    await session.commit()

    assert pruned == 1
    assert await session.get(AuditOutbox, old.id) is None
    assert await session.get(AuditOutbox, recent.id) is not None
    assert await session.get(AuditOutbox, undelivered.id) is not None
    survivor = await session.get(UserNotification, notif.id)
    assert survivor is not None
    await session.refresh(survivor)  # FK SET NULL happened DB-side; reload it
    assert survivor.outbox_id is None


async def test_prune_dead_lettered_outbox(session, org_a):
    old = await _audit_outbox(
        session, org_id=org_a.id, dead_lettered_at=NOW - timedelta(days=100)
    )
    recent = await _audit_outbox(
        session, org_id=org_a.id, dead_lettered_at=NOW - timedelta(days=30)
    )
    await session.commit()

    pruned = await outbox_maintenance.prune_dead_lettered_outbox(session, NOW)
    await session.commit()

    assert pruned == 1
    assert await session.get(AuditOutbox, old.id) is None
    assert await session.get(AuditOutbox, recent.id) is not None


async def test_prune_read_notifications(session, org_a, analyst_a):
    cutoff_old = NOW - timedelta(days=100)
    recent = NOW - timedelta(days=10)

    def _mk_notif(created_at):
        n = UserNotification(
            organisation_id=org_a.id,
            user_id=None,
            event_type="case.created",
            title="t",
            created_at=created_at,
        )
        session.add(n)
        return n

    old_read = _mk_notif(cutoff_old)
    old_unread = _mk_notif(cutoff_old)
    recent_read = _mk_notif(recent)
    await session.flush()
    for n in (old_read, recent_read):
        session.add(
            UserNotificationRead(
                notification_id=n.id, user_id=analyst_a.id, read_at=n.created_at
            )
        )
    await session.commit()

    pruned = await outbox_maintenance.prune_read_notifications(session, NOW)
    await session.commit()

    assert pruned == 1  # only the old, read notification
    assert await session.get(UserNotification, old_read.id) is None
    assert await session.get(UserNotification, old_unread.id) is not None  # unread kept
    assert await session.get(UserNotification, recent_read.id) is not None  # recent kept
    # Its read receipt cascaded away with it.
    remaining_reads = await session.scalar(
        select(func.count()).select_from(UserNotificationRead)
    )
    assert remaining_reads == 1


async def test_prune_old_notifier_deliveries(session, org_a):
    notifier = Notifier(
        organisation_id=org_a.id, type=NotifierType.webhook, target="x", created_by="t"
    )
    session.add(notifier)
    await session.flush()
    old = NotifierDelivery(
        notifier_id=notifier.id, status="sent", created_at=NOW - timedelta(days=40)
    )
    recent = NotifierDelivery(
        notifier_id=notifier.id, status="sent", created_at=NOW - timedelta(days=10)
    )
    session.add(old)
    session.add(recent)
    await session.commit()

    pruned = await outbox_maintenance.prune_old_notifier_deliveries(session, NOW)
    await session.commit()

    assert pruned == 1
    assert await session.get(NotifierDelivery, old.id) is None
    assert await session.get(NotifierDelivery, recent.id) is not None


async def test_maintenance_sweep_runs_outbox_prunes(session, org_a):
    """The 30s sweep entry point includes the outbox retention counts."""
    await _audit_outbox(
        session, org_id=org_a.id, delivered_at=NOW - timedelta(days=40)
    )
    await session.commit()

    result = await outbox_maintenance.run_outbox_maintenance_sweep(session, NOW)
    assert result["pruned_delivered_outbox"] == 1


# --- Ops surface: dead-letter count in the overview --------------------------


async def test_overview_reports_dead_letter_count(session, org_a):
    await _audit_outbox(
        session, org_id=org_a.id, dead_lettered_at=NOW - timedelta(days=1)
    )
    await _audit_outbox(session, org_id=org_a.id)  # healthy, uncounted
    await session.commit()

    overview = await overview_crud.build_overview(session, org_a.id)
    assert overview.dead_letter_count == 1
