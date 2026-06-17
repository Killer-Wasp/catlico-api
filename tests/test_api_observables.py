"""Tests for the Observables milestone: string observables, within-case dedup,
attachment-type rejection, case/alert parenting, soft-delete, import-on-promote."""
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user):
    return await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


async def test_create_and_list_case_observable(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4", "ioc": True},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"] == "1.2.3.4"
    assert r.json()["ioc"] is True

    lst = await client.get(f"/api/v1/cases/{case.id}/observables", headers=h)
    assert lst.json()["total"] == 1


async def test_within_case_dedup_conflicts(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    body = {"observable_type": "ip", "data": "9.9.9.9"}
    assert (await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)).status_code == 201
    dup = await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)
    assert dup.status_code == 409


async def test_attachment_type_rejected(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "file", "data": "evil.exe"},
        headers=h,
    )
    assert r.status_code == 422
    assert "not yet supported" in r.json()["detail"]


async def test_unknown_type_rejected(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "bogus", "data": "x"},
        headers=h,
    )
    assert r.status_code == 422


async def test_soft_delete_frees_dedup_slot(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    body = {"observable_type": "domain", "data": "evil.test"}
    r = await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)
    obs_id = r.json()["id"]
    assert (await client.delete(f"/api/v1/observables/{obs_id}", headers=h)).status_code == 204
    assert (await client.get(f"/api/v1/observables/{obs_id}", headers=h)).status_code == 404
    # Re-adding the same value now succeeds
    r2 = await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)
    assert r2.status_code == 201
    assert r2.json()["id"] != obs_id


async def test_observable_not_visible_to_other_org(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    observable_types,
    analyst_a,
    analyst_a_token,
    analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": "1.1.1.1"},
        headers=h,
    )
    obs_id = r.json()["id"]
    hb = _headers(analyst_b_token, org_b.id)
    assert (await client.get(f"/api/v1/observables/{obs_id}", headers=hb)).status_code == 404


async def test_promote_imports_alert_observables(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    # Create an alert and attach two observables
    r = await client.post(
        "/api/v1/alerts/",
        json={"type": "phishing", "source": "gw", "source_ref": "e1", "title": "a"},
        headers=h,
    )
    alert_id = r.json()["id"]
    for data in ("5.5.5.5", "bad.test"):
        t = "ip" if data[0].isdigit() else "domain"
        await client.post(
            f"/api/v1/alerts/{alert_id}/observables",
            json={"observable_type": t, "data": data},
            headers=h,
        )

    promo = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    case_id = promo.json()["id"]
    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.json()["total"] == 2
    datas = {o["data"] for o in obs.json()["items"]}
    assert datas == {"5.5.5.5", "bad.test"}
