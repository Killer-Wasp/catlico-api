"""Event push to runners: outbox consumer enqueues deliveries; poller signs/POSTs."""
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlmodel import select

from app.api.deps import PluginRunnerPrincipal
from app.api.internal.routes.plugin_runner import submit_result
from app.core.configs import settings
from app.core.crypto import encrypt_string
from app.models.audit import AuditOutbox
from app.models.plugin_runner import (
    PluginDefinition,
    PluginEventDelivery,
    PluginRunner,
    PluginVersion,
)
from app.models.plugin_runner import OrgPlugin
from app.models.plugin_runner import PluginRun
from app.services.plugin_dispatch import (
    _enqueue_for_healthy_runners,
    _envelope_from_run,
    build_manual_envelope,
    build_plugin_envelope,
    manual_event_id,
    plugin_event_consumer,
    push_pending_deliveries,
    redeliver_run,
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


def test_manual_event_id_is_deterministic():
    a = manual_event_id("org-a", "acme", "observable", "obs-1")
    assert a == manual_event_id("org-a", "acme", "observable", "obs-1")
    assert a != manual_event_id("org-a", "acme", "observable", "obs-2")
    assert a != manual_event_id("org-a", "other", "observable", "obs-1")


async def test_manual_envelope_enqueues_and_pushes_per_healthy_runner(session, org_a):
    r1 = await _seed_runner(session, "r1")
    r2 = await _seed_runner(session, "r2", base_url="http://runner2:8090")
    envelope = build_manual_envelope(
        org_id=org_a.id, plugin_id="acme", entity_type="observable",
        entity_id="obs-1", actor="user:u1",
    )
    created = await _enqueue_for_healthy_runners(session, envelope)
    await session.commit()

    assert created == 2
    deliveries = (await session.execute(select(PluginEventDelivery))).scalars().all()
    assert {d.runner_id for d in deliveries} == {r1.id, r2.id}
    assert all(d.status == "pending" for d in deliveries)
    assert all(d.envelope["manual"] is True for d in deliveries)
    assert all(d.envelope["target_plugin_id"] == "acme" for d in deliveries)
    assert all(d.envelope["event_type"] == "observable.manual" for d in deliveries)

    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={"dispatched": 1})

    result = await push_pending_deliveries(session, transport=httpx.MockTransport(handler))
    assert result["delivered"] == 2
    assert len(sent) == 2


async def test_redeliver_run_resets_delivered_delivery_to_pending(session, org_a):
    runner = await _seed_runner(session)
    await _seed_subscribed_plugin(session)
    run = PluginRun(
        event_id="audit:x", event_type="observable.created", organisation_id=org_a.id,
        plugin_id="acme", runner_id=runner.id, status="queued", attempt=2,
        event_object_type="observable", event_object_id="obs-1",
    )
    session.add(run)
    session.add(
        PluginEventDelivery(
            event_id="audit:x", runner_id=runner.id, envelope={"event_id": "audit:x"},
            status="delivered", delivered_at=datetime.now(UTC),
            next_attempt_at=datetime.now(UTC), attempts=3,
        )
    )
    await session.flush()

    await redeliver_run(session, run)

    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.status == "pending"
    assert delivery.delivered_at is None
    assert delivery.attempts == 0
    assert delivery.next_attempt_at <= datetime.now(UTC)


async def test_redeliver_run_synthesizes_when_no_delivery_survives(session, org_a):
    runner = await _seed_runner(session)
    await _seed_subscribed_plugin(session)
    run = PluginRun(
        event_id="manual:org-a:acme:observable:obs-1", event_type="observable.manual",
        organisation_id=org_a.id, plugin_id="acme", runner_id=runner.id,
        status="queued", attempt=2, event_object_type="observable",
        event_object_id="obs-1", created_by="user-9",
    )
    session.add(run)
    await session.flush()

    await redeliver_run(session, run)

    delivery = (await session.execute(select(PluginEventDelivery))).scalar_one()
    assert delivery.status == "pending"
    assert delivery.runner_id == runner.id
    # Manual-ness recovered from the .manual event_type, not from runner input.
    assert delivery.envelope["manual"] is True
    assert delivery.envelope["target_plugin_id"] == "acme"


def test_envelope_from_run_marks_non_manual_as_system():
    run = PluginRun(
        event_id="audit:y", event_type="observable.created", organisation_id="org-a",
        plugin_id="acme", runner_id="r1", event_object_type="observable",
        event_object_id="obs-1",
    )
    env = _envelope_from_run(run)
    assert "manual" not in env
    assert env["actor"] == "system"


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


# --- Auto-retry of transient failures in submit_result (Task 12) ---
async def _seed_run_for_result(session, org_id, *, attempt=1):
    """Seed a runner + enabled plugin + a running PluginRun ready to submit_result."""
    runner = await _seed_runner(session, runner_id=f"r-{uuid.uuid4().hex[:8]}")
    pdef = PluginDefinition(id=f"p-{uuid.uuid4().hex[:8]}", display_name="P")
    session.add(pdef)
    await session.flush()
    version_id = f"{pdef.id}@1.0.0"
    session.add(
        PluginVersion(id=version_id, plugin_id=pdef.id, version="1.0.0", status="active")
    )
    session.add(OrgPlugin(organisation_id=org_id, plugin_id=pdef.id, enabled=True))
    run = PluginRun(
        event_id=f"evt-{uuid.uuid4().hex[:8]}",
        event_type="observable.created",
        organisation_id=org_id,
        plugin_id=pdef.id,
        plugin_version_id=version_id,
        runner_id=runner.id,
        status="running",
        attempt=attempt,
        runtime_token_hash="live-token-hash",
        runtime_token_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return runner, pdef, run


async def test_submit_result_transient_failure_requeues(session, org_a):
    runner, pdef, run = await _seed_run_for_result(session, org_a.id, attempt=1)
    principal = PluginRunnerPrincipal(runner_id=runner.id)

    resp = await submit_result(
        run.id,
        {"status": "failure", "error_kind": "transient", "log_tail": "boom"},
        principal,
        session,
    )
    await session.flush()
    await session.refresh(run)

    # Re-queued (reusing the same row), attempt bumped, terminal fields cleared.
    assert resp["status"] == "queued"
    assert run.status == "queued"
    assert run.attempt == 2
    assert run.error is None
    assert run.error_kind is None
    assert run.started_at is None
    assert run.ended_at is None
    # Old runtime token invalidated on the re-queue path.
    assert run.runtime_token_hash is None
    assert run.runtime_token_expires_at is None
    # Diagnostics from the failed attempt are preserved.
    assert run.log_tail == "boom"
    # redeliver_run made the delivery re-pending.
    delivery = (
        await session.execute(
            select(PluginEventDelivery).where(
                PluginEventDelivery.event_id == run.event_id,
                PluginEventDelivery.runner_id == runner.id,
            )
        )
    ).scalar_one()
    assert delivery.status == "pending"
    # Circuit breaker NOT recorded: no config-failure streak movement.
    op = await session.get(OrgPlugin, (org_a.id, pdef.id))
    assert op.config_failure_streak == 0
    # Exactly one run row for (event_id, plugin_id) — no second insert.
    runs = (
        await session.execute(
            select(PluginRun).where(
                PluginRun.event_id == run.event_id, PluginRun.plugin_id == pdef.id
            )
        )
    ).scalars().all()
    assert len(runs) == 1


async def test_submit_result_transient_gives_up_at_cap(session, org_a):
    cap = settings.PLUGIN_TRANSIENT_MAX_ATTEMPTS
    runner, pdef, run = await _seed_run_for_result(session, org_a.id, attempt=cap)
    principal = PluginRunnerPrincipal(runner_id=runner.id)

    resp = await submit_result(
        run.id,
        {"status": "failure", "error_kind": "transient", "log_tail": "boom"},
        principal,
        session,
    )
    await session.flush()
    await session.refresh(run)

    # At the cap a transient failure terminalizes rather than re-queueing.
    assert resp["status"] == "failure"
    assert run.status == "failure"
    assert run.attempt == cap
    assert run.error_kind == "transient"
    assert run.ended_at is not None
    # Terminal path leaves the token resolvable (Task 11), does not null it.
    assert run.runtime_token_hash == "live-token-hash"
    # A terminal TRANSIENT failure is not a config signal: breaker untouched.
    op = await session.get(OrgPlugin, (org_a.id, pdef.id))
    assert op.config_failure_streak == 0


async def test_submit_result_config_failure_terminalizes_and_records_breaker(session, org_a):
    runner, pdef, run = await _seed_run_for_result(session, org_a.id, attempt=1)
    principal = PluginRunnerPrincipal(runner_id=runner.id)

    resp = await submit_result(
        run.id,
        {"status": "failure", "error_kind": "config"},
        principal,
        session,
    )
    await session.flush()
    await session.refresh(run)

    # Non-transient failure never retries: terminal immediately.
    assert resp["status"] == "failure"
    assert run.status == "failure"
    assert run.attempt == 1
    # Circuit breaker recorded the config failure.
    op = await session.get(OrgPlugin, (org_a.id, pdef.id))
    assert op.config_failure_streak == 1


async def test_submit_result_success_terminalizes(session, org_a):
    runner, pdef, run = await _seed_run_for_result(session, org_a.id, attempt=1)
    principal = PluginRunnerPrincipal(runner_id=runner.id)

    resp = await submit_result(
        run.id,
        {"status": "success", "result_summary": {"verdict": "info"}},
        principal,
        session,
    )
    await session.flush()
    await session.refresh(run)

    assert resp["status"] == "success"
    assert run.status == "success"
    assert run.attempt == 1
    assert run.ended_at is not None
