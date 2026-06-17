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
