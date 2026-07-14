"""A3: Notifier delivery — rule matching, Webhook, Slack.

Registered as an outbox consumer so every committed event is matched against
enabled notification rules, and matching notifiers fire exactly once per event.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secrets
from app.models.audit import AuditOutbox
from app.models.notification import (
    Notifier,
    NotifierDelivery,
    NotifierType,
    NotificationRule,
)
from app.services.net_guard import guarded_post
from app.services.outbox_events import build_event_envelope
from app.services.smtp import send_email

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


def _destination_url(notifier: Notifier) -> str:
    """The real delivery URL, read from the encrypted secrets blob (not the
    plaintext `target`, which is now only a display label)."""
    url = (decrypt_secrets(notifier.secrets_encrypted).get("url") or "").strip()
    if not url:
        raise ValueError(f"notifier {notifier.id} has no destination URL in secrets")
    return url


async def _send_webhook(notifier: Notifier, payload: dict) -> None:
    """POST the event envelope to the webhook URL (from encrypted secrets), through
    the SSRF guard. If a `signing_secret` is stored, add an
    `X-Catlico-Signature: sha256=<hex>` header — an HMAC-SHA256 over the exact raw
    request body (receivers verify by recomputing it over the bytes they receive).
    """
    url = _destination_url(notifier)
    secrets = decrypt_secrets(notifier.secrets_encrypted)
    body = json.dumps(payload).encode()
    headers = {"content-type": "application/json"}
    signing_secret = secrets.get("signing_secret")
    if signing_secret:
        sig = hmac.new(signing_secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Catlico-Signature"] = f"sha256={sig}"
    resp = await guarded_post(_client, url, content=body, headers=headers)
    resp.raise_for_status()


async def _send_slack(notifier: Notifier, payload: dict) -> None:
    """Send a Slack incoming-webhook message. The webhook URL is a secret (read
    from the encrypted secrets blob, never `target`), so the POST goes through the
    SSRF guard like any other outbound delivery."""
    url = _destination_url(notifier)
    envelope = payload
    event_type = envelope.get("event_type", "unknown")
    actor = envelope.get("actor", "system")
    obj = envelope.get("object", {})
    text = f"*{event_type}* by {actor}\n{obj.get('type', '?')}: {obj.get('id', '?')}"
    message = {"text": text}
    resp = await guarded_post(_client, url, json=message)
    resp.raise_for_status()


def _build_email_body(envelope: dict) -> str:
    """A plain-text summary of the event for an email notifier body."""
    obj = envelope.get("object", {}) or {}
    ctx = envelope.get("context", {}) or {}
    lines = [
        f"Event: {envelope.get('event_type', 'unknown')}",
        f"Actor: {envelope.get('actor', 'system')}",
        f"Entity: {obj.get('type', '?')} {obj.get('id', '?')}".rstrip(),
        f"Context: {ctx.get('type', '?')} {ctx.get('id', '?')}".rstrip(),
        f"Timestamp: {envelope.get('created_at') or '?'}",
    ]
    return "\n".join(lines) + "\n"


async def _send_email(notifier: Notifier, payload: dict) -> None:
    """Email an event summary to the notifier's configured recipients.

    Recipients come from ``notifier.config["recipients"]`` (a list of addresses);
    ``notifier.target`` is only a display label. Raises when no recipients are
    configured or when SMTP is unconfigured (the delivery consumer records either
    as a failed delivery — an admin needs to see the notifier isn't delivering).
    """
    recipients = notifier.config.get("recipients") or []
    if not recipients:
        raise ValueError("email notifier has no recipients configured")
    event_type = payload.get("event_type", "unknown")
    actor = payload.get("actor", "system")
    subject = f"[catlico] {event_type} by {actor}"
    body = _build_email_body(payload)
    await send_email(recipients, subject, body)


_SENDERS = {
    NotifierType.webhook: _send_webhook,
    NotifierType.slack: _send_slack,
    NotifierType.email: _send_email,
    # kafka is an explicit non-delivering type for now
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
                # kafka is an explicit non-delivering type
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
