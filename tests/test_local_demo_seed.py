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
from app.models.case_ import Case


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
