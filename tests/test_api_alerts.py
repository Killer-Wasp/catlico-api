"""Tests for the Alerts milestone (grill 2026-06-13): org-owned visibility,
status lifecycle, upsert dedup, promote-to-case, soft-delete, per-org flag."""
from datetime import UTC, datetime

from httpx import AsyncClient


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _alert_payload(**overrides):
    base = {
        "type": "phishing",
        "source": "mail-gw",
        "source_ref": "evt-001",
        "title": "Suspicious email",
        "description": "user reported",
        "severity": 2,
    }
    base.update(overrides)
    return base


async def test_ingest_creates_then_upserts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)

    # First ingest -> 201 created
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    assert r.status_code == 201, r.text
    alert_id = r.json()["id"]
    assert r.json()["status"] == "New"

    # Same dedup key -> 200 updated (not a new row)
    r2 = await client.post(
        "/api/v1/alerts/",
        json=_alert_payload(title="Updated title", severity=3),
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == alert_id
    assert r2.json()["title"] == "Updated title"
    assert r2.json()["severity"] == 3

    # Only one alert exists
    lst = await client.get("/api/v1/alerts/", headers=h)
    assert lst.json()["total"] == 1


async def test_upsert_does_not_resurrect_ignored(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    # Ignore it
    await client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "Ignored"}, headers=h)
    # Re-ingest -> stays Ignored
    r2 = await client.post("/api/v1/alerts/", json=_alert_payload(title="again"), headers=h)
    assert r2.status_code == 200
    assert r2.json()["status"] == "Ignored"
    assert r2.json()["title"] == "again"


async def test_follow_false_blocks_resync(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    await client.patch(f"/api/v1/alerts/{alert_id}", json={"follow": False}, headers=h)
    # Re-ingest with new title -> no-op, title unchanged
    r2 = await client.post(
        "/api/v1/alerts/", json=_alert_payload(title="should not apply"), headers=h
    )
    assert r2.status_code == 200
    assert r2.json()["title"] == "Suspicious email"


async def test_alert_is_org_scoped(
    client: AsyncClient,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_a_token,
    analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=ha)
    alert_id = r.json()["id"]
    # org-b cannot see org-a's alert
    hb = _headers(analyst_b_token, org_b.id)
    assert (await client.get(f"/api/v1/alerts/{alert_id}", headers=hb)).status_code == 404


async def test_same_dedup_key_distinct_per_org(
    client: AsyncClient,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    # Same source event ingested into two orgs = two alerts.
    ra = await client.post(
        "/api/v1/alerts/", json=_alert_payload(), headers=_headers(analyst_a_token, org_a.id)
    )
    rb = await client.post(
        "/api/v1/alerts/", json=_alert_payload(), headers=_headers(analyst_b_token, org_b.id)
    )
    assert ra.status_code == 201
    assert rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]


async def test_illegal_status_transition_and_imported_guard(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    # Cannot set Imported directly
    r2 = await client.patch(f"/api/v1/alerts/{alert_id}", json={"status": "Imported"}, headers=h)
    assert r2.status_code == 422


async def test_promote_creates_case_and_links(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    event_date = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    r = await client.post(
        "/api/v1/alerts/",
        json=_alert_payload(severity=3, date=event_date.isoformat()),
        headers=h,
    )
    alert_id = r.json()["id"]

    promo = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    assert promo.status_code == 201, promo.text
    case = promo.json()
    assert case["title"] == "Suspicious email"
    assert case["severity"] == 3
    assert case["start_date"] is not None  # anchored to the alert event date

    # Alert now Imported and linked
    got = await client.get(f"/api/v1/alerts/{alert_id}", headers=h)
    assert got.json()["status"] == "Imported"
    assert got.json()["case_id"] == case["id"]


async def test_double_promote_conflicts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    assert (await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)).status_code == 201
    second = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    assert second.status_code == 409


async def test_promote_carries_procedures_to_case(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """§4.1b: an alert's TTPs are copied onto the case at promote (comments stay
    on the alert, not carried)."""
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]

    await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=h,
    )
    await client.post(
        f"/api/v1/alerts/{alert_id}/comments",
        json={"message": "triage note"},
        headers=h,
    )

    promo = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    assert promo.status_code == 201, promo.text
    case_id = promo.json()["id"]

    case_procs = await client.get(f"/api/v1/cases/{case_id}/procedures", headers=h)
    assert case_procs.status_code == 200, case_procs.text
    externals = {p["pattern"]["external_id"] for p in case_procs.json()}
    assert "T1566" in externals
    assert all(p["case_id"] == case_id for p in case_procs.json())

    # Comments STAY on the alert (readable via the case↔alert link), not moved.
    alert_comments = await client.get(
        f"/api/v1/alerts/{alert_id}/comments", headers=h
    )
    assert alert_comments.json()["total"] == 1
    case_comments = await client.get(f"/api/v1/cases/{case_id}/comments", headers=h)
    assert case_comments.json()["total"] == 0


async def test_promote_dedups_procedures_already_on_case(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """Carry-over is additive/deduped by pattern: a technique the case already
    carries isn't duplicated when the alert brings the same one."""
    from app.crud import case_ as case_crud
    from app.models.case_ import CaseCreate

    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=h,
    )

    # Pre-existing case already carrying T1566, then merge the alert into it.
    case = await case_crud.create_case(
        session,
        CaseCreate(title="target"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await session.commit()
    await client.put(
        f"/api/v1/cases/{case.id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=h,
    )
    merged = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [alert_id], "target_case_id": case.id},
        headers=h,
    )
    assert merged.status_code == 201, merged.text

    case_procs = await client.get(f"/api/v1/cases/{case.id}/procedures", headers=h)
    externals = [p["pattern"]["external_id"] for p in case_procs.json()]
    assert externals == ["T1566"]  # deduped, not duplicated


async def test_detach_unlinks_alert_and_returns_to_new(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    promo = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    assert promo.status_code == 201, promo.text

    detached = await client.post(f"/api/v1/alerts/{alert_id}/detach", headers=h)
    assert detached.status_code == 200, detached.text
    assert detached.json()["status"] == "New"
    assert detached.json()["case_id"] is None

    got = await client.get(f"/api/v1/alerts/{alert_id}", headers=h)
    assert got.json()["status"] == "New"
    assert got.json()["case_id"] is None

    # Re-promotable after detaching (no longer 409).
    assert (
        await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    ).status_code == 201


async def test_detach_unattached_alert_conflicts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    # Never promoted → not attached to any case.
    resp = await client.post(f"/api/v1/alerts/{alert_id}/detach", headers=h)
    assert resp.status_code == 409


async def test_detach_is_org_scoped(
    client: AsyncClient, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=ha)
    alert_id = r.json()["id"]
    assert (
        await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=ha)
    ).status_code == 201

    hb = _headers(analyst_b_token, org_b.id)
    # Org B cannot see or detach org A's alert.
    assert (
        await client.post(f"/api/v1/alerts/{alert_id}/detach", headers=hb)
    ).status_code == 404


async def test_soft_delete_hides_alert(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    assert (await client.delete(f"/api/v1/alerts/{alert_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/alerts/{alert_id}", headers=h)).status_code == 404
    # Dedup key freed: re-ingesting the same key creates a fresh alert
    r2 = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    assert r2.status_code == 201
    assert r2.json()["id"] != alert_id


async def test_flag_is_per_org(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    alert_id = r.json()["id"]
    assert (await client.put(f"/api/v1/alerts/{alert_id}/flag", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/alerts/{alert_id}", headers=h)).json()["flagged"] is True
    assert (await client.delete(f"/api/v1/alerts/{alert_id}/flag", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/alerts/{alert_id}", headers=h)).json()["flagged"] is False


async def test_readonly_cannot_ingest(
    client: AsyncClient, org_a, builtin_roles, readonly_a, readonly_a_token
):
    h = _headers(readonly_a_token, org_a.id)
    r = await client.post("/api/v1/alerts/", json=_alert_payload(), headers=h)
    assert r.status_code == 403
