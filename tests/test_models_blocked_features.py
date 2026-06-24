"""Verify the five blocked-feature tables (sla_policy, api_key, notifier,
notification_rule, knowledge_base_page, function, function_run) created by
migrations a1d3f5b7c9e2 through e6b8d1f3a5c7."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.core.db import init_db
from app.models.api_key import ApiKey
from app.models.function import Function, FunctionRun, FunctionRunStatus, FunctionRuntime, FunctionTrigger
from app.models.knowledge_base import KnowledgeBasePage
from app.models.notification import NotificationRule, Notifier, NotifierType
from app.models.sla import SlaPolicy


@pytest.fixture
async def org(session, monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    await init_db(session)
    return "catlico-demo"


# ── helpers ──────────────────────────────────────────────────────────────────

_ORG_ID = "catlico-demo"


# ── sla_policy ───────────────────────────────────────────────────────────────

async def test_sla_policy_insert(session, org):
    sla = SlaPolicy(
        organisation_id=org,
        severity=2,
        ack_seconds=900,
        resolve_seconds=14400,
        escalation_target="On-call lead",
        enabled=True,
        created_by="system",
    )
    session.add(sla)
    await session.commit()

    row = (await session.execute(select(SlaPolicy).where(SlaPolicy.id == sla.id))).scalar_one()
    assert row.severity == 2
    assert row.ack_seconds == 900
    assert row.resolve_seconds == 14400
    assert row.escalation_target == "On-call lead"
    assert row.enabled is True
    assert row.organisation_id == org
    assert row.created_at is not None
    assert row.updated_at is None


async def test_sla_policy_unique_org_severity(session, org):
    a = SlaPolicy(organisation_id=org, severity=1, ack_seconds=60, resolve_seconds=120, created_by="system")
    b = SlaPolicy(organisation_id=org, severity=1, ack_seconds=30, resolve_seconds=60, created_by="system")
    session.add(a)
    await session.commit()
    session.add(b)
    with pytest.raises(Exception):
        await session.commit()


# ── api_key ──────────────────────────────────────────────────────────────────

async def test_api_key_insert(session, org):
    key = ApiKey(
        id=uuid.uuid4(),
        organisation_id=org,
        name="test-key",
        prefix="thp_",
        last_four="3f9a",
        key_hash="abc123def456",
        scopes=["read:alert", "write:case"],
        last_used_at=None,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        created_by="system",
    )
    session.add(key)
    await session.commit()

    row = (await session.execute(select(ApiKey).where(ApiKey.id == key.id))).scalar_one()
    assert row.name == "test-key"
    assert row.prefix == "thp_"
    assert row.last_four == "3f9a"
    assert row.key_hash == "abc123def456"
    assert row.scopes == ["read:alert", "write:case"]
    assert row.last_used_at is None
    assert row.expires_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert row.deleted_at is None


async def test_api_key_soft_delete(session, org):
    key = ApiKey(
        id=uuid.uuid4(),
        organisation_id=org,
        name="revokable",
        prefix="",
        last_four="",
        key_hash="hash",
        created_by="system",
    )
    session.add(key)
    await session.commit()

    key.deleted_at = datetime.now(UTC)
    key.deleted_by = "system"
    await session.commit()

    row = (await session.execute(select(ApiKey).where(ApiKey.id == key.id))).scalar_one()
    assert row.deleted_at is not None
    assert row.deleted_by == "system"


async def test_api_key_key_hash_index(session):
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'api_key' AND indexname LIKE '%key_hash%'")
    )
    names = [r[0] for r in result]
    assert any("key_hash" in n for n in names)


# ── notifier ─────────────────────────────────────────────────────────────────

async def test_notifier_insert(session, org):
    n = Notifier(
        id=uuid.uuid4(),
        organisation_id=org,
        type=NotifierType.slack,
        target="#soc-alerts",
        config={"icon_emoji": ":warning:"},
        secrets_encrypted=None,
        enabled=True,
        created_by="system",
    )
    session.add(n)
    await session.commit()

    row = (await session.execute(select(Notifier).where(Notifier.id == n.id))).scalar_one()
    assert row.type == NotifierType.slack
    assert row.target == "#soc-alerts"
    assert row.config == {"icon_emoji": ":warning:"}
    assert row.secrets_encrypted is None
    assert row.enabled is True


async def test_notifier_defaults(session, org):
    n = Notifier(
        id=uuid.uuid4(),
        organisation_id=org,
        type=NotifierType.email,
        created_by="system",
    )
    session.add(n)
    await session.commit()

    row = (await session.execute(select(Notifier).where(Notifier.id == n.id))).scalar_one()
    assert row.target == ""
    assert row.config == {}
    assert row.enabled is True


# ── notification_rule ────────────────────────────────────────────────────────

async def test_notification_rule_insert(session, org):
    n_id = uuid.uuid4()
    r = NotificationRule(
        id=uuid.uuid4(),
        organisation_id=org,
        name="Critical alerts",
        description="Notify on critical alerts",
        event="alert.critical",
        enabled=True,
        notifier_ids=[str(n_id)],
        created_by="system",
    )
    session.add(r)
    await session.commit()

    row = (await session.execute(select(NotificationRule).where(NotificationRule.id == r.id))).scalar_one()
    assert row.name == "Critical alerts"
    assert row.event == "alert.critical"
    assert row.notifier_ids == [str(n_id)]
    assert row.enabled is True


async def test_notification_rule_defaults(session, org):
    r = NotificationRule(
        id=uuid.uuid4(),
        organisation_id=org,
        name="Minimal rule",
        created_by="system",
    )
    session.add(r)
    await session.commit()

    row = (await session.execute(select(NotificationRule).where(NotificationRule.id == r.id))).scalar_one()
    assert row.description == ""
    assert row.event is None
    assert row.notifier_ids == []
    assert row.enabled is True


# ── knowledge_base_page ──────────────────────────────────────────────────────

async def test_knowledge_base_page_insert(session, org):
    page = KnowledgeBasePage(
        organisation_id=org,
        title="Runbook: Phishing",
        summary="How to triage phishing alerts",
        tags=["phishing", "triage"],
        blocks=[
            {"type": "paragraph", "text": "Step 1: check headers", "code": None},
            {"type": "section", "title": "Indicators", "items": ["SPF fail", "DKIM mismatch"]},
        ],
        created_by="system",
    )
    session.add(page)
    await session.commit()

    row = (await session.execute(select(KnowledgeBasePage).where(KnowledgeBasePage.id == page.id))).scalar_one()
    assert row.title == "Runbook: Phishing"
    assert row.summary == "How to triage phishing alerts"
    assert row.tags == ["phishing", "triage"]
    assert len(row.blocks) == 2
    assert row.blocks[0]["type"] == "paragraph"
    assert row.deleted_at is None


async def test_knowledge_base_page_soft_delete(session, org):
    page = KnowledgeBasePage(
        organisation_id=org,
        title="To be deleted",
        created_by="system",
    )
    session.add(page)
    await session.commit()

    page.deleted_at = datetime.now(UTC)
    page.deleted_by = "system"
    await session.commit()

    row = (await session.execute(select(KnowledgeBasePage).where(KnowledgeBasePage.id == page.id))).scalar_one()
    assert row.deleted_at is not None


async def test_knowledge_base_page_title_index(session):
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_base_page' AND indexname LIKE '%title%'")
    )
    names = [r[0] for r in result]
    assert any("title" in n for n in names)


# ── function ─────────────────────────────────────────────────────────────────

async def test_function_insert(session, org):
    f = Function(
        organisation_id=org,
        name="Auto-tag alerts",
        description="Tag incoming alerts by source",
        runtime=FunctionRuntime.python,
        trigger=FunctionTrigger.event,
        trigger_config={"event_types": ["alert.created"]},
        profile="analyst",
        enabled=False,
        timeout_ms=30000,
        egress="",
        approval=False,
        code="def run(event):\n    pass\n",
        secrets=["vault/slack_token"],
        run_count=0,
        error_count=0,
        created_by="system",
    )
    session.add(f)
    await session.commit()

    row = (await session.execute(select(Function).where(Function.id == f.id))).scalar_one()
    assert row.name == "Auto-tag alerts"
    assert row.runtime == FunctionRuntime.python
    assert row.trigger == FunctionTrigger.event
    assert row.trigger_config == {"event_types": ["alert.created"]}
    assert row.profile == "analyst"
    assert row.enabled is False
    assert row.timeout_ms == 30000
    assert row.egress == ""
    assert row.approval is False
    assert row.code == "def run(event):\n    pass\n"
    assert row.secrets == ["vault/slack_token"]
    assert row.run_count == 0
    assert row.error_count == 0
    assert row.deleted_at is None


async def test_function_defaults(session, org):
    f = Function(
        organisation_id=org,
        name="Minimal function",
        created_by="system",
    )
    session.add(f)
    await session.commit()

    row = (await session.execute(select(Function).where(Function.id == f.id))).scalar_one()
    assert row.description == ""
    assert row.runtime == FunctionRuntime.javascript
    assert row.trigger == FunctionTrigger.event
    assert row.trigger_config == {}
    assert row.profile == "analyst"
    assert row.enabled is False
    assert row.timeout_ms == 15000
    assert row.egress == ""
    assert row.approval is False
    assert row.code == ""
    assert row.secrets == []
    assert row.run_count == 0
    assert row.error_count == 0


async def test_function_soft_delete(session, org):
    f = Function(
        organisation_id=org,
        name="Old function",
        created_by="system",
    )
    session.add(f)
    await session.commit()

    f.deleted_at = datetime.now(UTC)
    f.deleted_by = "system"
    await session.commit()

    row = (await session.execute(select(Function).where(Function.id == f.id))).scalar_one()
    assert row.deleted_at is not None


# ── function_run ─────────────────────────────────────────────────────────────

async def test_function_run_insert(session, org):
    f = Function(
        organisation_id=org,
        name="Runner",
        created_by="system",
    )
    session.add(f)
    await session.commit()

    run = FunctionRun(
        id=uuid.uuid4(),
        function_id=f.id,
        status=FunctionRunStatus.success,
        trigger="manual",
        started_at=datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC),
        duration_ms=1234,
        attempts=1,
        error=None,
        created_by="system",
    )
    session.add(run)
    await session.commit()

    row = (await session.execute(select(FunctionRun).where(FunctionRun.id == run.id))).scalar_one()
    assert row.function_id == f.id
    assert row.status == FunctionRunStatus.success
    assert row.trigger == "manual"
    assert row.duration_ms == 1234
    assert row.attempts == 1
    assert row.error is None
    assert row.created_at is not None


async def test_function_run_cascade_delete(session, org):
    f = Function(
        organisation_id=org,
        name="Cascade test",
        created_by="system",
    )
    session.add(f)
    await session.commit()

    run = FunctionRun(
        id=uuid.uuid4(),
        function_id=f.id,
        status=FunctionRunStatus.failure,
        trigger="event",
        started_at=datetime.now(UTC),
        duration_ms=500,
        attempts=2,
        error="timeout",
        created_by="system",
    )
    session.add(run)
    await session.commit()

    await session.delete(f)
    await session.commit()

    result = await session.execute(select(FunctionRun).where(FunctionRun.function_id == f.id))
    assert result.scalar_one_or_none() is None


# ── permission backfill (from migrations 4 & 5) ──────────────────────────────

async def test_knowledge_base_permissions_present(session, org):
    """init_db seeds roles + backfilled knowledge_base permissions."""
    for perm in ("read:knowledge_base", "write:knowledge_base"):
        result = await session.execute(
            text(f"SELECT 1 FROM role_permission WHERE permission = '{perm}'")
        )
        assert result.all(), f"Missing {perm}"


async def test_function_permissions_present(session, org):
    """init_db seeds roles + backfilled function permissions."""
    for perm in ("read:function", "run:function", "write:function"):
        result = await session.execute(
            text(f"SELECT 1 FROM role_permission WHERE permission = '{perm}'")
        )
        assert result.all(), f"Missing {perm}"


# ── role permission backfill ─────────────────────────────────────────────────

async def test_upsert_builtin_role_backfills_new_permissions(session):
    """When a built-in role exists but is missing a newly added permission,
    upsert_builtin_role adds the missing permission without touching existing ones."""
    from app.models.role import Role, RolePermission, Permission
    from app.crud.role import upsert_builtin_role

    # Create a minimal role manually (simulating an old role)
    role = Role(name="test-custom", created_by="system")
    session.add(role)
    await session.flush()
    session.add(RolePermission(role_id=role.id, permission="read:case"))
    await session.commit()

    # Now "upsert" with a larger permission set
    await upsert_builtin_role(
        session,
        "test-custom",
        {Permission.read_case, Permission.write_case, Permission.read_alert},
        created_by="system",
    )

    perms = await session.execute(
        text(f"SELECT permission FROM role_permission WHERE role_id = '{role.id}'")
    )
    perm_set = {row[0] for row in perms}
    assert "read:case" in perm_set  # original preserved
    assert "write:case" in perm_set  # newly backfilled
    assert "read:alert" in perm_set  # newly backfilled
