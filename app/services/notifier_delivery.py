"""A3: Notifier delivery — rule matching, Webhook, Slack.

Registered as an outbox consumer so every committed event is matched against
enabled notification rules, and matching notifiers fire exactly once per event.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditOutbox
from app.models.notification import (
    Notifier,
    NotifierDelivery,
    NotifierType,
    NotificationRule,
)
from app.services.outbox_events import build_event_envelope

logger = logging.getLogger(__name__)

# ponytail: global httpx client with timeouts — per-notifier clients if needed
_client = httpx.AsyncClient(timeout=30.0)


async def _get_or_create_delivery(
    session: AsyncSession, outbox_id: int, notifier_id
) -> NotifierDelivery:
    """Fetch or create a delivery row, returning the existing one if present."""
    stmt = pg_insert(NotifierDelivery).values(
        outbox_id=outbox_id,
        notifier_id=notifier_id,
    ).on_conflict_do_nothing(
        index_elements=["outbox_id", "notifier_id"]
    )
    await session.execute(stmt)
    await session.flush()
    result = await session.execute(
        select(NotifierDelivery).where(
            NotifierDelivery.outbox_id == outbox_id,
            NotifierDelivery.notifier_id == notifier_id,
        )
    )
    return result.scalar_one()


async def _send_webhook(notifier: Notifier, payload: dict) -> None:
    """POST JSON payload to the webhook URL."""
    target = notifier.target.strip()
    if not target:
        logger.warning("webhook notifier %s has no target URL", notifier.id)
        raise ValueError("Empty webhook target")
    resp = await _client.post(target, json=payload)
    resp.raise_for_status()


async def _send_slack(notifier: Notifier, payload: dict) -> None:
    """Send a Slack incoming-webhook message.

    The `target` is the Slack webhook URL (kept in config/secrets, not plaintext
    in the public API). Build a simple formatted message from the event.
    """
    target = notifier.target.strip()
    if not target:
        raise ValueError("Empty Slack webhook target")
    envelope = payload
    event_type = envelope.get("event_type", "unknown")
    actor = envelope.get("actor", "system")
    obj = envelope.get("object", {})
    text = f"*{event_type}* by {actor}\n{obj.get('type', '?')}: {obj.get('id', '?')}"
    message = {"text": text}
    resp = await _client.post(target, json=message)
    resp.raise_for_status()


_SENDERS = {
    NotifierType.webhook: _send_webhook,
    NotifierType.slack: _send_slack,
    # email and kafka are explicit non-delivering types for now
}


async def notifier_delivery_consumer(session: AsyncSession, row: AuditOutbox) -> None:
    """Outbox consumer: match rules → deliver to notifiers.

    For each enabled matching rule, find enabled notifiers and send.
    Delivery is idempotent: each (outbox, notifier) pair fires at most once.
    """
    envelope = build_event_envelope(row)
    event_type = envelope["event_type"]
    org_id = row.payload.get("organisation_id")
    if not org_id:
        return  # ponytail: skip events without org scoping

    # Find matching rules for this event type
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.organisation_id == org_id,
            NotificationRule.enabled.is_(True),
            (NotificationRule.event == event_type) | (NotificationRule.event == "AnyEvent"),
        )
    )
    rules = result.scalars().all()

    for rule in rules:
        for notifier_id in rule.notifier_ids:
            # Fetch notifier
            notifier_result = await session.execute(
                select(Notifier).where(
                    Notifier.id == notifier_id,
                    Notifier.organisation_id == org_id,
                    Notifier.enabled.is_(True),
                )
            )
            notifier = notifier_result.scalar_one_or_none()
            if notifier is None:
                continue
            sender = _SENDERS.get(notifier.type)
            if sender is None:
                # email/kafka are explicit non-delivering types
                continue

            # Idempotency: fetch or create delivery row
            delivery = await _get_or_create_delivery(
                session, row.id, notifier.id
            )
            if delivery.status == "sent":
                continue  # already delivered

            delivery.attempts += 1
            session.add(delivery)
            try:
                await sender(notifier, envelope)
            except Exception as exc:
                delivery.status = "failed"
                delivery.last_error = str(exc)[:1000]
                session.add(delivery)
                logger.warning(
                    "notifier delivery failed outbox=%d notifier=%s: %s",
                    row.id, notifier.id, exc
                )
            else:
                delivery.status = "sent"
                delivery.sent_at = datetime.now(UTC)
                session.add(delivery)
