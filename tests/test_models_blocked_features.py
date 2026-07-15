"""Verify the blocked-feature tables (sla_policy, api_key, notifier,
notification_rule, knowledge_base_page) created by migrations
a1d3f5b7c9e2 onward."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import init_db
from app.models.api_key import ApiKey
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
    # The demo seed gives the org an SLA policy per severity; clear them so this
    # test owns the (org, severity) space it asserts on.
    await session.execute(delete(SlaPolicy).where(SlaPolicy.organisation_id == org))
    await session.commit()
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
    # The demo seed already occupies severities 1-4 for this org; clear them so
    # the duplicate we insert below is the one that trips the constraint.
    await session.execute(delete(SlaPolicy).where(SlaPolicy.organisation_id == org))
    await session.commit()
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
        content="## Indicators\n\n- SPF fail\n- DKIM mismatch",
        created_by="system",
    )
    session.add(page)
    await session.commit()

    row = (await session.execute(select(KnowledgeBasePage).where(KnowledgeBasePage.id == page.id))).scalar_one()
    assert row.title == "Runbook: Phishing"
    assert row.summary == "How to triage phishing alerts"
    assert row.tags == ["phishing", "triage"]
    assert row.content == "## Indicators\n\n- SPF fail\n- DKIM mismatch"
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


async def test_knowledge_base_page_version_insert(session, org_a, analyst_a):
    from sqlmodel import select

    from app.models.knowledge_base import (
        KnowledgeBasePage,
        KnowledgeBasePageVersion,
    )

    page = KnowledgeBasePage(
        organisation_id=org_a.id,
        title="Runbook",
        summary="A phishing runbook",
        tags=["phishing"],
        content="Initial content",
        created_by=str(analyst_a.id),
    )
    session.add(page)
    await session.flush()

    version = KnowledgeBasePageVersion(
        page_id=page.id,
        organisation_id=org_a.id,
        version_number=1,
        action="create",
        snapshot={
            "title": "Runbook",
            "summary": "A phishing runbook",
            "tags": ["phishing"],
            "content": "Initial content",
        },
        changed_fields=["title", "summary", "tags", "content"],
        edited_by=str(analyst_a.id),
        edited_by_email=analyst_a.email,
    )
    session.add(version)
    await session.flush()

    result = await session.execute(select(KnowledgeBasePageVersion))
    saved = result.scalar_one()
    assert saved.page_id == page.id
    assert saved.version_number == 1
    assert saved.action == "create"
    assert saved.snapshot["content"] == "Initial content"
    assert saved.changed_fields == ["title", "summary", "tags", "content"]
    assert saved.edited_by_email == analyst_a.email


async def test_knowledge_base_page_version_rejects_duplicate_page_version(
    session, org_a, analyst_a
):
    from app.models.knowledge_base import (
        KnowledgeBasePage,
        KnowledgeBasePageVersion,
    )

    page = KnowledgeBasePage(
        organisation_id=org_a.id,
        title="Runbook",
        created_by=str(analyst_a.id),
    )
    session.add(page)
    await session.flush()

    session.add(
        KnowledgeBasePageVersion(
            page_id=page.id,
            organisation_id=org_a.id,
            version_number=1,
            action="create",
            snapshot={"title": "Runbook", "summary": "", "tags": [], "content": ""},
            changed_fields=["title"],
            edited_by=str(analyst_a.id),
            edited_by_email=analyst_a.email,
        )
    )
    await session.flush()

    session.add(
        KnowledgeBasePageVersion(
            page_id=page.id,
            organisation_id=org_a.id,
            version_number=1,
            action="update",
            snapshot={"title": "Runbook", "summary": "", "tags": [], "content": ""},
            changed_fields=["content"],
            edited_by=str(analyst_a.id),
            edited_by_email=analyst_a.email,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_knowledge_base_page_version_rejects_unknown_action(
    session, org_a, analyst_a
):
    from app.models.knowledge_base import (
        KnowledgeBasePage,
        KnowledgeBasePageVersion,
    )

    page = KnowledgeBasePage(
        organisation_id=org_a.id,
        title="Runbook",
        created_by=str(analyst_a.id),
    )
    session.add(page)
    await session.flush()

    session.add(
        KnowledgeBasePageVersion(
            page_id=page.id,
            organisation_id=org_a.id,
            version_number=1,
            action="publish",
            snapshot={"title": "Runbook", "summary": "", "tags": [], "content": ""},
            changed_fields=["content"],
            edited_by=str(analyst_a.id),
            edited_by_email=analyst_a.email,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_knowledge_base_page_title_index(session):
    result = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_base_page' AND indexname LIKE '%title%'")
    )
    names = [r[0] for r in result]
    assert any("title" in n for n in names)


# ── permission backfill (from migrations 4 & 5) ──────────────────────────────

async def test_intel_permissions_present(session, org):
    """init_db seeds roles carrying the flat admin grants: custom fields, knowledge
    base, connectors and enrichment all fold into manage:org, and member/role admin
    into manage:users."""
    for perm in ("manage:org", "manage:users"):
        result = await session.execute(
            text(f"SELECT 1 FROM role_permission WHERE permission = '{perm}'")
        )
        assert result.all(), f"Missing {perm}"


# ── role permission backfill ─────────────────────────────────────────────────

async def test_upsert_builtin_role_backfills_new_permissions(session, org):
    """When a built-in role exists but is missing a newly added permission,
    upsert_builtin_role adds the missing permission without touching existing ones."""
    from app.models.role import Role, RolePermission, Permission
    from app.crud.role import upsert_builtin_role

    # Create a minimal role manually (simulating an old role)
    role = Role(name="test-custom", organisation_id=org, created_by="system")
    session.add(role)
    await session.flush()
    session.add(RolePermission(role_id=role.id, permission="read:case"))
    await session.commit()

    # Now "upsert" with a larger permission set
    await upsert_builtin_role(
        session,
        "test-custom",
        {
            Permission.read_case,
            Permission.write_case,
            Permission.manage_org,
        },
        org,
        created_by="system",
    )

    perms = await session.execute(
        text(f"SELECT permission FROM role_permission WHERE role_id = '{role.id}'")
    )
    perm_set = {row[0] for row in perms}
    assert "read:case" in perm_set  # original preserved
    assert "write:case" in perm_set  # newly backfilled
    assert "manage:org" in perm_set  # newly backfilled
