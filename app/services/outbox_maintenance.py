"""Retention pruning for the audit outbox and the in-app notification tables.

Mirrors ``app.services.plugin_maintenance``: every unit is a plain async function
that executes its deletes on the caller's session (no commit) so a test can drive
one pass deterministically with an injected ``now``; ``run_outbox_maintenance_sweep``
composes them and commits once.

Because the outbox child FKs are ON DELETE SET NULL (migration c4e8f2a6b0d9),
pruning an ``audit_outbox`` row leaves its child ``user_notification`` /
``notifier_delivery`` rows intact — their ``outbox_id`` just goes NULL.

What this sweep prunes: read in-app notifications (with their read receipts),
delivered and dead-lettered outbox rows, and old notifier-delivery rows — each on
its own retention window.

What it deliberately does NOT prune: **unread** in-app notifications are kept
regardless of age. In particular org-wide notifications (``user_id=None``) that no
user ever reads never gain a read receipt, so ``prune_read_notifications`` leaves
them in place — a real, intentional retention of unread rows, not an oversight.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.models.audit import AuditOutbox
from app.models.notification import NotifierDelivery, UserNotification, UserNotificationRead

logger = logging.getLogger(__name__)


async def prune_delivered_outbox(session: AsyncSession, now: datetime) -> int:
    """Delete delivered outbox rows past the retention window."""
    cutoff = now - timedelta(days=settings.OUTBOX_RETENTION_DAYS)
    result = await session.execute(
        delete(AuditOutbox).where(
            AuditOutbox.delivered_at.isnot(None),
            AuditOutbox.delivered_at < cutoff,
        )
    )
    return result.rowcount or 0


async def prune_dead_lettered_outbox(session: AsyncSession, now: datetime) -> int:
    """Delete dead-lettered outbox rows past their (longer) retention window."""
    cutoff = now - timedelta(days=settings.OUTBOX_DEAD_LETTER_RETENTION_DAYS)
    result = await session.execute(
        delete(AuditOutbox).where(
            AuditOutbox.dead_lettered_at.isnot(None),
            AuditOutbox.dead_lettered_at < cutoff,
        )
    )
    return result.rowcount or 0


async def prune_read_notifications(session: AsyncSession, now: datetime) -> int:
    """Delete read in-app notifications past the retention window. A notification
    counts as read iff a ``user_notification_read`` receipt exists for it; deleting
    it cascades those receipts away. Unread notifications are kept regardless of
    age (they're still actionable)."""
    cutoff = now - timedelta(days=settings.NOTIFICATION_READ_RETENTION_DAYS)
    read_exists = (
        select(UserNotificationRead.id)
        .where(UserNotificationRead.notification_id == UserNotification.id)
        .exists()
    )
    result = await session.execute(
        delete(UserNotification).where(
            UserNotification.created_at < cutoff,
            read_exists,
        )
    )
    return result.rowcount or 0


async def prune_old_notifier_deliveries(session: AsyncSession, now: datetime) -> int:
    """Delete notifier delivery-ledger rows past the retention window."""
    cutoff = now - timedelta(days=settings.NOTIFIER_DELIVERY_RETENTION_DAYS)
    result = await session.execute(
        delete(NotifierDelivery).where(NotifierDelivery.created_at < cutoff)
    )
    return result.rowcount or 0


async def run_outbox_maintenance_sweep(
    session: AsyncSession, now: datetime | None = None
) -> dict:
    """One outbox/notification retention pass. Returns counts for observability."""
    now = now or datetime.now(UTC)
    pruned_delivered = await prune_delivered_outbox(session, now)
    pruned_dead_lettered = await prune_dead_lettered_outbox(session, now)
    pruned_notifications = await prune_read_notifications(session, now)
    pruned_deliveries = await prune_old_notifier_deliveries(session, now)
    await session.commit()
    return {
        "pruned_delivered_outbox": pruned_delivered,
        "pruned_dead_lettered_outbox": pruned_dead_lettered,
        "pruned_read_notifications": pruned_notifications,
        "pruned_notifier_deliveries": pruned_deliveries,
    }
