"""Tests for bulk alert->case merge (POST /alerts/merge): merge into a new case
or an existing one, observable dedup, TLP floor, and eligibility guards."""
from httpx import AsyncClient


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _alert(**overrides):
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


async def _ingest(client, h, **overrides):
    r = await client.post("/api/v1/alerts/", json=_alert(**overrides), headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_merge_into_new_case(
    client: AsyncClient, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    a1 = await _ingest(client, h, source_ref="e1", severity=2, tlp=1, title="A1")
    a2 = await _ingest(client, h, source_ref="e2", severity=4, tlp=3, title="A2")
    await client.post(
        f"/api/v1/alerts/{a1}/observables",
        json={"observable_type": "ip", "data": "1.1.1.1"},
        headers=h,
    )
    await client.post(
        f"/api/v1/alerts/{a2}/observables",
        json={"observable_type": "domain", "data": "evil.test"},
        headers=h,
    )

    resp = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [a1, a2], "title": "combined"},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()
    assert case["title"] == "combined"
    assert case["severity"] == 4  # max across alerts
    assert case["tlp"] == 3  # max (most restrictive)

    obs = await client.get(f"/api/v1/cases/{case['id']}/observables", headers=h)
    assert sorted(o["data"] for o in obs.json()["items"]) == ["1.1.1.1", "evil.test"]

    for aid in (a1, a2):
        got = await client.get(f"/api/v1/alerts/{aid}", headers=h)
        assert got.json()["status"] == "Imported"
        assert got.json()["case_id"] == case["id"]


async def test_merge_into_existing_case_dedups_and_raises_tlp(
    client: AsyncClient, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    # Existing case at tlp 1, with one observable already present.
    case_resp = await client.post(
        "/api/v1/cases/", json={"title": "host", "tlp": 1}, headers=h
    )
    case_id = case_resp.json()["id"]
    await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": "1.1.1.1"},
        headers=h,
    )

    a1 = await _ingest(client, h, source_ref="e1", tlp=3)
    # Same IP as the case (dedup) + a new one.
    await client.post(
        f"/api/v1/alerts/{a1}/observables",
        json={"observable_type": "ip", "data": "1.1.1.1"},
        headers=h,
    )
    await client.post(
        f"/api/v1/alerts/{a1}/observables",
        json={"observable_type": "ip", "data": "2.2.2.2"},
        headers=h,
    )

    resp = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [a1], "target_case_id": case_id},
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["tlp"] == 3  # raised to the alert's more restrictive TLP

    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert sorted(o["data"] for o in obs.json()["items"]) == ["1.1.1.1", "2.2.2.2"]


async def test_merge_rejects_already_promoted(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    a1 = await _ingest(client, h, source_ref="e1")
    a2 = await _ingest(client, h, source_ref="e2")
    await client.post(f"/api/v1/alerts/{a1}/promote", json={}, headers=h)
    resp = await client.post(
        "/api/v1/alerts/merge", json={"alert_ids": [a1, a2]}, headers=h
    )
    assert resp.status_code == 409, resp.text


async def test_merge_empty_list_422(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    resp = await client.post(
        "/api/v1/alerts/merge", json={"alert_ids": []}, headers=_h(analyst_a_token, org_a.id)
    )
    assert resp.status_code == 422, resp.text


async def test_merge_cross_org_alert_not_found(
    client: AsyncClient, org_a, org_b, builtin_roles, analyst_a, analyst_b,
    analyst_a_token, analyst_b_token,
):
    a_a = await _ingest(client, _h(analyst_a_token, org_a.id), source_ref="e1")
    a_b = await _ingest(client, _h(analyst_b_token, org_b.id), source_ref="e2")
    # org-a cannot reference org-b's alert.
    resp = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [a_a, a_b]},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert resp.status_code == 404, resp.text


async def test_merge_into_other_org_case_not_found(
    client: AsyncClient, session, org_a, org_b, builtin_roles, analyst_a, analyst_b,
    analyst_a_token, analyst_b_token,
):
    from app.crud import case_ as case_crud
    from app.models.case_ import CaseCreate

    case = await case_crud.create_case(
        session, CaseCreate(title="b-case"),
        owner_org_id=org_b.id, owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_b.id),
    )
    await session.commit()
    a1 = await _ingest(client, _h(analyst_a_token, org_a.id), source_ref="e1")
    resp = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [a1], "target_case_id": case.id},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert resp.status_code == 404, resp.text


async def test_merge_requires_write_perms(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token,
    readonly_a, readonly_a_token,
):
    a1 = await _ingest(client, _h(analyst_a_token, org_a.id), source_ref="e1")
    a2 = await _ingest(client, _h(analyst_a_token, org_a.id), source_ref="e2")
    resp = await client.post(
        "/api/v1/alerts/merge",
        json={"alert_ids": [a1, a2]},
        headers=_h(readonly_a_token, org_a.id),
    )
    assert resp.status_code == 403, resp.text
