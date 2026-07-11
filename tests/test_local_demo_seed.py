"""Smoke test for the local demo seeder: verifies each demo case is created and
that its originating alert is linked (promoted) to it."""

from sqlmodel import select

from app.core.demo_seed_data import (
    EXFILTRATION_CASE_ALERT_REF,
    OAUTH_CASE_ALERT_REF,
    RANSOMWARE_CASE_ALERT_REF,
)
from app.core.local_demo_seed import seed_local_demo_data
from app.models.alert import Alert, AlertStatus
from app.models.case_ import Case, CaseStatus
from app.models.dashboard import Dashboard
from app.models.sla import SlaPolicy
from app.models.user import User


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


async def test_seed_populates_dashboard_data(session, builtin_roles):
    """The dashboard-focused seed data lands: resolved cases (with dispositions),
    unassigned open cases, SLA policies and the extra analyst team."""
    await seed_local_demo_data(session)

    cases = (await session.execute(select(Case))).scalars().all()

    # Historical resolved cases with recorded dispositions.
    resolved = [c for c in cases if c.status == CaseStatus.resolved]
    assert len(resolved) >= 5
    assert all(c.resolution_status is not None for c in resolved)
    # Backdated so the case-trend / MTTR windows have history to show.
    assert any(c.created_at < c.updated_at for c in resolved)

    # Unassigned open cases feed the "New" pipeline bucket.
    assert any(
        c.status == CaseStatus.open and c.assignee_id is None for c in cases
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
