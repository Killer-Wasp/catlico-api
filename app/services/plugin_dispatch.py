"""Event push to plugin runners.

Two stages, kept apart so DB work and network I/O never share a transaction:

1. ``plugin_event_consumer`` runs inside the audit-outbox drain: for each
   committed event that some installed plugin cares about, it enqueues one
   ``PluginEventDelivery`` (pending) per healthy runner. Plugin-actor events are
   suppressed here (loop prevention).
2. ``push_pending_deliveries`` runs in its own poller: it POSTs each pending
   envelope to the runner's ``/internal/events`` with the HMAC signature the
   runner verifies, applying exponential backoff and a max-age expiry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.configs import settings
from app.core.crypto import decrypt_string
from app.models.audit import AuditOutbox
from app.models.plugin_runner import (
    OrgPlugin,
    PluginDefinition,
    PluginEventDelivery,
    PluginRun,
    PluginRunner,
)
from app.services.outbox_events import build_event_envelope

SCHEDULE_EVENT_TYPE = "schedule.fired"
MANUAL_EVENT_TYPE_SUFFIX = ".manual"

logger = logging.getLogger(__name__)


def build_plugin_envelope(row: AuditOutbox) -> dict:
    """Plugin event envelope: the shared audit envelope plus the fields the SDK's
    ``PluginEvent.from_envelope`` needs (organisation_id, data)."""
    envelope = build_event_envelope(row)
    payload = row.payload or {}
    envelope["organisation_id"] = payload.get("organisation_id", "")
    envelope["data"] = payload.get("details", {})
    envelope["attempt"] = 1
    return envelope


def _signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def plugin_event_consumer(session: AsyncSession, row: AuditOutbox) -> None:
    """Outbox consumer: queue one delivery per healthy runner for events that a
    plugin actually subscribes to. No network I/O here."""
    payload = row.payload or {}
    actor = payload.get("actor", "")
    if isinstance(actor, str) and actor.startswith("plugin:"):
        return  # loop prevention: plugin-caused events are never redispatched

    envelope = build_plugin_envelope(row)
    event_type = envelope["event_type"]

    definitions = (
        (
            await session.execute(
                select(PluginDefinition).where(
                    PluginDefinition.active_version_id.isnot(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if not any(
        event_type in (d.manifest or {}).get("triggers", []) for d in definitions
    ):
        return  # no installed plugin wants this event

    await _enqueue_for_healthy_runners(session, envelope)


async def _enqueue_for_healthy_runners(session: AsyncSession, envelope: dict) -> int:
    """Create one pending delivery per healthy runner, skipping ones already
    queued for this event (idempotent across drain retries and cron sweeps)."""
    runners = (
        (
            await session.execute(
                select(PluginRunner).where(
                    PluginRunner.status == "healthy",
                    PluginRunner.enrollment_state == "enrolled",
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    created = 0
    for runner in runners:
        exists = (
            await session.execute(
                select(PluginEventDelivery.id).where(
                    PluginEventDelivery.event_id == envelope["event_id"],
                    PluginEventDelivery.runner_id == runner.id,
                )
            )
        ).first()
        if exists is not None:
            continue
        session.add(
            PluginEventDelivery(
                event_id=envelope["event_id"],
                runner_id=runner.id,
                envelope=envelope,
                status="pending",
                next_attempt_at=now,
            )
        )
        created += 1
    return created


def manual_event_id(
    org_id: str, plugin_id: str, entity_type: str, entity_id: str
) -> str:
    """Deterministic id per (org, plugin, entity). Being deterministic is the
    whole point: a redelivered push or a double-clicked "run" button resolves to
    the same event_id, so the ``(event_id, plugin_id)`` unique constraint stops
    the plugin from running twice."""
    return f"manual:{org_id}:{plugin_id}:{entity_type}:{entity_id}"


def build_manual_envelope(
    *, org_id: str, plugin_id: str, entity_type: str, entity_id: str, actor: str
) -> dict:
    """Synthesize an on-demand manual-run envelope (mirrors the cron precedent in
    ``schedule_due_events``). ``target_plugin_id`` makes the runner execute
    exactly one plugin; ``manual`` is the server-side flag ``create_run`` reads
    back to grant the analyst-intent relaxations."""
    return {
        "event_id": manual_event_id(org_id, plugin_id, entity_type, entity_id),
        "event_type": f"{entity_type}{MANUAL_EVENT_TYPE_SUFFIX}",
        "organisation_id": org_id,
        "actor": actor,
        "object": {"type": entity_type, "id": entity_id},
        "context": {"type": "unknown", "id": ""},
        "data": {},
        "manual": True,
        "target_plugin_id": plugin_id,
        "attempt": 1,
    }


def _envelope_from_run(run: PluginRun) -> dict:
    """Reconstruct a delivery envelope from a run row, for retries where the
    original delivery did not survive retention. Manual-ness is recovered from
    the API-authored ``.manual`` event_type — never from runner input."""
    manual = (run.event_type or "").endswith(MANUAL_EVENT_TYPE_SUFFIX)
    envelope = {
        "event_id": run.event_id,
        "event_type": run.event_type,
        "organisation_id": run.organisation_id,
        "actor": f"user:{run.created_by}" if manual and run.created_by else "system",
        "object": {
            "type": run.event_object_type or "",
            "id": run.event_object_id or "",
        },
        "context": {"type": "unknown", "id": ""},
        "data": {},
        "attempt": run.attempt,
    }
    if manual:
        envelope["manual"] = True
        envelope["target_plugin_id"] = run.plugin_id
    return envelope


async def redeliver_run(
    session: AsyncSession, run: PluginRun, now: datetime | None = None
) -> None:
    """Make the push loop resend this run's event.

    ``_enqueue_for_healthy_runners`` skips when a delivery already exists, so a
    naive re-enqueue of an already-``delivered`` row is a silent no-op. Reset the
    surviving ``(event_id, runner_id)`` delivery back to ``pending`` with a fresh
    age window; if none survived retention, synthesize one from the run.
    """
    now = now or datetime.now(UTC)
    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(
                PluginEventDelivery.event_id == run.event_id,
                PluginEventDelivery.runner_id == run.runner_id,
            )
        )
    ).scalar_one_or_none()
    if delivery is not None:
        delivery.status = "pending"
        delivery.attempts = 0
        delivery.last_error = None
        delivery.delivered_at = None
        delivery.next_attempt_at = now
        # Restart the max-age clock, else _reschedule may expire a resend of an
        # old delivery on its first failure.
        delivery.created_at = now
    else:
        session.add(
            PluginEventDelivery(
                event_id=run.event_id,
                runner_id=run.runner_id,
                envelope=_envelope_from_run(run),
                status="pending",
                next_attempt_at=now,
            )
        )


def _schedule_event_id(plugin_id: str, org_id: str, fire_time: datetime) -> str:
    """Deterministic id per (plugin, org, cron slot) — dedupes multi-runner firing
    and re-emission across sweeps."""
    return f"schedule:{plugin_id}:{org_id}:{int(fire_time.timestamp())}"


async def schedule_due_events(session: AsyncSession, now: datetime | None = None) -> int:
    """Emit ``schedule.fired`` events for plugins whose cron slot has passed.

    Runs in the periodic sweep (API-side, never runner-side, so N runners cannot
    fire N times). Uses the most-recent slot only, so a scheduler outage produces
    at most one catch-up event per (plugin, org). Returns events emitted.
    """
    from croniter import croniter

    now = now or datetime.now(UTC)
    definitions = (
        (
            await session.execute(
                select(PluginDefinition).where(
                    PluginDefinition.active_version_id.isnot(None)
                )
            )
        )
        .scalars()
        .all()
    )
    scheduled = [
        d for d in definitions
        if SCHEDULE_EVENT_TYPE in (d.manifest or {}).get("triggers", [])
    ]
    emitted = 0
    for pdef in scheduled:
        default_cron = (pdef.manifest or {}).get("schedule")
        org_plugins = (
            (
                await session.execute(
                    select(OrgPlugin).where(
                        OrgPlugin.plugin_id == pdef.id,
                        OrgPlugin.enabled.is_(True),
                        OrgPlugin.auto_run_enabled.is_(True),
                        OrgPlugin.suspended_reason.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for op in org_plugins:
            cron = op.schedule_override or default_cron
            if not cron or not croniter.is_valid(cron):
                continue
            fire_time = croniter(cron, now).get_prev(datetime)
            event_id = _schedule_event_id(pdef.id, op.organisation_id, fire_time)
            already = (
                await session.execute(
                    select(PluginEventDelivery.id).where(
                        PluginEventDelivery.event_id == event_id
                    )
                )
            ).first()
            if already is not None:
                continue
            envelope = {
                "event_id": event_id,
                "event_type": SCHEDULE_EVENT_TYPE,
                "organisation_id": op.organisation_id,
                "actor": "system",
                "object": {"type": "schedule", "id": pdef.id},
                "context": {"type": "unknown", "id": ""},
                "data": {"fire_time": fire_time.isoformat(), "cron": cron},
                "attempt": 1,
            }
            if await _enqueue_for_healthy_runners(session, envelope):
                emitted += 1
    return emitted


def _reschedule(delivery: PluginEventDelivery, now: datetime, error: str) -> None:
    delivery.attempts += 1
    delivery.last_error = error
    age = now - (
        delivery.created_at
        if delivery.created_at and delivery.created_at.tzinfo
        else (delivery.created_at or now).replace(tzinfo=UTC)
    )
    if age > timedelta(seconds=settings.PLUGIN_PUSH_MAX_AGE_SECONDS):
        delivery.status = "expired"
        return
    backoff = min(
        settings.PLUGIN_PUSH_BACKOFF_CAP_SECONDS,
        settings.PLUGIN_PUSH_BACKOFF_BASE_SECONDS * (2 ** (delivery.attempts - 1)),
    )
    delivery.status = "pending"
    delivery.next_attempt_at = now + timedelta(seconds=backoff)


async def push_pending_deliveries(
    session: AsyncSession,
    now: datetime | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    limit: int = 100,
) -> dict:
    """POST due pending deliveries to their runners. Returns counts for tests."""
    now = now or datetime.now(UTC)
    due = (
        (
            await session.execute(
                select(PluginEventDelivery)
                .where(
                    PluginEventDelivery.status == "pending",
                    PluginEventDelivery.next_attempt_at <= now,
                )
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    delivered = failed = expired = 0
    for delivery in due:
        runner = await session.get(PluginRunner, delivery.runner_id)
        secret = decrypt_string(runner.push_signing_secret_encrypted) if runner else None
        if not runner or not runner.base_url or not secret:
            _reschedule(delivery, now, "runner not reachable/enrolled")
            failed += 1
            continue
        body = json.dumps(delivery.envelope).encode()
        headers = {
            "content-type": "application/json",
            "x-catlico-signature": _signature(body, secret),
        }
        url = f"{runner.base_url.rstrip('/')}/internal/events"
        try:
            async with httpx.AsyncClient(
                transport=transport, timeout=settings.PLUGIN_PUSH_TIMEOUT_SECONDS
            ) as client:
                resp = await client.post(url, content=body, headers=headers)
            if resp.status_code < 300:
                delivery.status = "delivered"
                delivery.delivered_at = now
                delivered += 1
            else:
                _reschedule(delivery, now, f"HTTP {resp.status_code}")
                failed += 1
        except Exception as exc:  # noqa: BLE001 — network error -> retry
            _reschedule(delivery, now, f"{type(exc).__name__}: {exc}")
            failed += 1
        if delivery.status == "expired":
            expired += 1
    await session.commit()
    return {"delivered": delivered, "failed": failed, "expired": expired}
