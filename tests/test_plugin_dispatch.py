"""Event push to runners: outbox consumer enqueues deliveries; poller signs/POSTs."""
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import select

from app.core.crypto import encrypt_string
from app.models.audit import AuditOutbox
from app.models.plugin_runner import (
    PluginDefinition,
    PluginEventDelivery,
    PluginRunner,
    PluginVersion,
)
from app.models.plugin_runner import OrgPlugin
from app.services.plugin_dispatch import (
    build_plugin_envelope,
    plugin_event_consumer,
    push_pending_deliveries,
    schedule_due_events,
)

PUSH_SECRET = "runner-push-secret"


async def _seed_runner(session, runner_id="r1", *, base_url="http://runner:8090"):
    runner = PluginRunner(
        id=runner_id, status="healthy", enrollment_state="enrolled",
        base_url=base_url, push_signing_secret_encrypted=encrypt_string(PUSH_SECRET),
    )
    session.add(runner)
    await session.flush()
    return runner


async def _seed_subscribed_plugin(session, trigger="observable.created"):
    pdef = PluginDefinition(id="acme", display_name="Acme", manifest={"triggers": [trigger]})
    session.add(pdef)
    await session.flush()
    session.add(
        PluginVersion(id="acme@1", plugin_id="acme", version="1", status="active",
                      manifest={"triggers": [trigger]})
    )
    await session.flush()
    pdef.active_version_id = "acme@1"
    await session.flush()
    return pdef


def _outbox_row(action="create", object_type="observable", actor="user-1"):
    return AuditOutbox(
        audit_id=1,
        topic="audit",
        payload={
            "object_type": object_type,
            "action": action,
            "object_id": "obs-1",
            "organisation_id": "org-a",
            "actor": actor,
            "details": {"observable_type": "ip"},
        },
    )


def test_build_plugin_envelope_normalizes_and_adds_org():
    envelope = build_plugin_envelope(_outbox_row())
    assert envelope["event_type"] == "observable.created"  # past tense
    assert envelope["organisation_id"] == "org-a"
    assert envelope["object"] == {"type": "observable", "id": "obs-1"}


async def test_consumer_enqueues_delivery_for_subscribed_event(session, org_a):
    runner = await _seed_runner(session)
    await _seed_subscribed_plugin(session)

    await plugin_event_consumer(session, _outbox_row())
    await session.flush()

    deliveries = (await session.execute(select(PluginEventDelivery))).scalars().all()
    assert len(deliveries) == 1
    assert deliveries[0].runner_id == runner.id
    assert deliveries[0].status == "pending"
    assert deliveries[0].envelope["event_type"] == "observable.created"


async def test_consumer_suppresses_plugin_actor_events(session, org_a):
    await _seed_runner(session)
    await _seed_subscribed_plugin(session)

    await plugin_event_consumer(session, _outbox_row(actor="plugin:acme@1"))
    await session.flush()

    assert (await session.execute(select(PluginEventDelivery))).first() is None


async def test_consumer_skips_events_no_plugin_wants(session, org_a):
    await _seed_runner(session)
    await _seed_subscribed_plugin(session, trigger="observable.created")

    # A comment.created event: no installed plugin subscribes.
    await plugin_event_consumer(session, _outbox_row(object_type="comment"))
    await session.flush()
    assert (await session.execute(select(PluginEventDelivery))).first() is None


async def test_push_delivers_with_valid_signature(session, org_a):
    runner = await _seed_runner(session)
    envelope = {"event_id": "audit:1", "event_type": "observable.created",
                "organisation_id": "org-a", "object": {"type": "observable", "id": "obs-1"}}
    session.add(
        PluginEventDelivery(
            event_id="audit:1", runner_id=runner.id, envelope=envelope,
            status="pending", next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await session.commit()

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sig"] = request.headers.get("x-catlico-signature")
        captured["body"] = request.content
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"dispatched": 1})

    result = await push_pending_deliveries(
        session, transport=httpx.MockTransport(handler)
    )
    assert result["delivered"] == 1
    # Signature matches HMAC-SHA256(secret, body).
    expected = "sha256=" + hmac.new(
        PUSH_SECRET.encode(), captured["body"], hashlib.sha256
    ).hexdigest()
    assert captured["sig"] == expected
    assert captured["url"].endswith("/internal/events")

    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.status == "delivered"


async def test_push_retries_on_failure(session, org_a):
    runner = await _seed_runner(session)
    session.add(
        PluginEventDelivery(
            event_id="audit:2", runner_id=runner.id, envelope={"event_id": "audit:2"},
            status="pending", next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await session.commit()

    result = await push_pending_deliveries(
        session, transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    assert result["failed"] == 1
    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.status == "pending"
    assert delivery.attempts == 1
    assert delivery.next_attempt_at > datetime.now(UTC)  # backed off


async def _seed_scheduled_plugin(session, org_id, *, cron_override=None, manifest_cron="0 */6 * * *"):
    manifest = {"triggers": ["schedule.fired"], "schedule": manifest_cron}
    pdef = PluginDefinition(id="sched", display_name="Sched", manifest=manifest)
    session.add(pdef)
    await session.flush()
    session.add(PluginVersion(id="sched@1", plugin_id="sched", version="1",
                              status="active", manifest=manifest))
    await session.flush()
    pdef.active_version_id = "sched@1"
    session.add(OrgPlugin(
        organisation_id=org_id, plugin_id="sched", enabled=True,
        auto_run_enabled=True, schedule_override=cron_override,
    ))
    await session.flush()
    return pdef


async def test_scheduler_emits_schedule_fired_event(session, org_a):
    await _seed_runner(session)
    await _seed_scheduled_plugin(session, org_a.id)

    emitted = await schedule_due_events(session)
    assert emitted == 1
    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.envelope["event_type"] == "schedule.fired"
    assert delivery.envelope["organisation_id"] == org_a.id
    assert delivery.envelope["object"] == {"type": "schedule", "id": "sched"}


async def test_scheduler_is_idempotent_within_a_slot(session, org_a):
    await _seed_runner(session)
    await _seed_scheduled_plugin(session, org_a.id)
    now = datetime(2026, 7, 10, 13, 0, 0, tzinfo=UTC)

    assert await schedule_due_events(session, now) == 1
    # Same cron slot -> no second emission.
    assert await schedule_due_events(session, now) == 0
    assert len((await session.execute(select(PluginEventDelivery))).scalars().all()) == 1


async def test_scheduler_skips_disabled_org(session, org_a):
    await _seed_runner(session)
    pdef = await _seed_scheduled_plugin(session, org_a.id)
    op = await session.get(OrgPlugin, (org_a.id, "sched"))
    op.auto_run_enabled = False
    await session.flush()

    assert await schedule_due_events(session) == 0


async def test_scheduler_honors_org_override(session, org_a):
    await _seed_runner(session)
    # Override to a yearly cron so its most-recent slot differs from the default.
    await _seed_scheduled_plugin(session, org_a.id, cron_override="0 0 1 1 *")
    now = datetime(2026, 7, 10, 13, 0, 0, tzinfo=UTC)

    assert await schedule_due_events(session, now) == 1
    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    # 2026-01-01 slot from the override, not the 6-hourly default.
    assert "2026-01-01" in delivery.envelope["data"]["fire_time"]


async def test_push_expires_past_max_age(session, org_a):
    runner = await _seed_runner(session)
    old = PluginEventDelivery(
        event_id="audit:3", runner_id=runner.id, envelope={"event_id": "audit:3"},
        status="pending", next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    session.add(old)
    await session.flush()
    # Force the created_at well past the max age window.
    old.created_at = datetime.now(UTC) - timedelta(days=2)
    await session.commit()

    result = await push_pending_deliveries(
        session, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    assert result["expired"] == 1
    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.status == "expired"
