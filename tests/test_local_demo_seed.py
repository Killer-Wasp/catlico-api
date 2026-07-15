"""Smoke test for the local demo seeder: verifies each demo case is created and
that its originating alert is linked (promoted) to it."""

from sqlmodel import select

from app.core.seed import seed_local_demo_data
from app.models.alert import Alert, AlertStatus
from app.models.case_ import Case
from app.models.case_status import CaseStage, CaseStatus
from app.models.dashboard import Dashboard
from app.models.log import Log
from app.models.sla import SlaPolicy
from app.models.task import Task
from app.models.user import User

# Originating alert refs promoted into the three narrative cases (demo profile).
OAUTH_CASE_ALERT_REF = "AL-9119"
RANSOMWARE_CASE_ALERT_REF = "AL-9123"
EXFILTRATION_CASE_ALERT_REF = "AL-9088"


async def test_seed_links_alerts_to_cases(session, builtin_roles):
    await seed_local_demo_data(session)

    cases = (await session.execute(select(Case))).scalars().all()
    titles = {c.title for c in cases}
    assert "OAuth consent grant — privileged account compromise" in titles
    assert "Ransomware precursor activity — lateral movement detected" in titles
    assert "Data exfiltration via unapproved SaaS application" in titles

    by_ref = {
        a.source_ref: a
        for a in (await session.execute(select(Alert))).scalars().all()
    }

    for ref in (OAUTH_CASE_ALERT_REF, RANSOMWARE_CASE_ALERT_REF, EXFILTRATION_CASE_ALERT_REF):
        alert = by_ref[ref]
        assert alert.case_id is not None, f"{ref} not linked to a case"
        assert alert.status == AlertStatus.imported, f"{ref} not marked Imported"
        assert alert.case_id in {c.id for c in cases}

    # Idempotent: a second run must not double-seed or break the links.
    await seed_local_demo_data(session)
    assert len((await session.execute(select(Case))).scalars().all()) == len(cases)


async def test_seed_creates_rich_tasks_and_work_logs(session, builtin_roles):
    """Demo tasks carry rich Markdown descriptions (not the case-title repeat of
    old), and the in-progress tasks get Markdown work logs seeded."""
    await seed_local_demo_data(session)

    tasks = (await session.execute(select(Task))).scalars().all()
    by_title = {t.title: t for t in tasks}

    # Task descriptions are real Markdown, not the old title-repeat placeholder.
    remove_rules = by_title["Remove mailbox rules and check forwarding"]
    assert "**Objective:**" in remove_rules.description
    # No longer the old placeholder (the case title repeated verbatim).
    assert (
        remove_rules.description
        != "OAuth consent grant — privileged account compromise"
    )

    # Work logs landed on the in-progress tasks and are Markdown.
    logs = (await session.execute(select(Log))).scalars().all()
    assert len(logs) >= 4
    assert any(log.message.startswith("Pulled inbox rules") for log in logs)
    assert all("case_title" not in log.message for log in logs)

    # Idempotent: re-running does not duplicate work logs.
    log_count = len(logs)
    await seed_local_demo_data(session)
    assert len((await session.execute(select(Log))).scalars().all()) == log_count


async def test_seed_populates_dashboard_data(session, builtin_roles):
    """The dashboard-focused seed data lands: resolved cases (with dispositions),
    unassigned open cases, SLA policies and the extra analyst team."""
    await seed_local_demo_data(session)

    cases = (await session.execute(select(Case))).scalars().all()
    stage_by_id = {
        s.id: s.stage
        for s in (await session.execute(select(CaseStatus))).scalars().all()
    }

    # Historical resolved (closed-stage) cases with recorded dispositions.
    resolved = [c for c in cases if stage_by_id.get(c.status_id) == CaseStage.closed]
    assert len(resolved) >= 5
    assert all(c.resolution_status is not None for c in resolved)
    # Backdated so the case-trend / MTTR windows have history to show.
    assert any(c.created_at < c.updated_at for c in resolved)

    # Unassigned open cases feed the "New" pipeline bucket.
    assert any(
        stage_by_id.get(c.status_id) == CaseStage.open and c.assignee_id is None
        for c in cases
    )

    # SLA policies exist for every severity (so breaches can compute).
    policies = (await session.execute(select(SlaPolicy))).scalars().all()
    assert {p.severity for p in policies} == {1, 2, 3, 4}

    # The extra analyst team was created.
    emails = {
        u.email for u in (await session.execute(select(User))).scalars().all()
    }
    assert {"priya.nguyen@example.com", "sam.iyer@example.com"} <= emails

    # Idempotent: a second run adds nothing.
    before = len(cases)
    await seed_local_demo_data(session)
    assert len((await session.execute(select(Case))).scalars().all()) == before


async def test_seed_creates_mock_dashboards(session, builtin_roles):
    """Ready-made dashboard views seed: two shared with the org, plus private
    ones — and each carries a widget layout."""
    await seed_local_demo_data(session)

    boards = (await session.execute(select(Dashboard))).scalars().all()
    by_name = {b.name: b for b in boards}

    assert {"SOC operations", "Alert triage", "My caseload"} <= set(by_name)
    # Sharing flags match the spec.
    assert by_name["SOC operations"].is_public is True
    assert by_name["Alert triage"].is_public is True
    assert by_name["My caseload"].is_public is False
    # Every board has a non-empty widget layout.
    assert all(b.layout.get("widgets") for b in boards)

    # Idempotent: re-seeding does not duplicate dashboards.
    count = len(boards)
    await seed_local_demo_data(session)
    assert len((await session.execute(select(Dashboard))).scalars().all()) == count
