"""G2+G3: Metric definitions, per-case metric values, and dashboards CRUD."""
import pytest
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


# --- Metric definitions ---


async def test_metric_crud(client: AsyncClient, org_a, analyst_a, analyst_a_token):
    h = _headers(analyst_a_token, org_a.id)
    c = await client.post(
        "/api/v1/metrics",
        json={"name": "dwell-time", "data_type": "number"},
        headers=h,
    )
    assert c.status_code == 201, c.text
    mid = c.json()["id"]

    assert any(
        m["id"] == mid
        for m in (await client.get("/api/v1/metrics", headers=h)).json()
    )

    upd = await client.patch(
        f"/api/v1/metrics/{mid}", json={"description": "hrs"}, headers=h
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["description"] == "hrs"

    assert (await client.delete(f"/api/v1/metrics/{mid}", headers=h)).status_code == 204


async def test_metric_readonly_forbidden(
    client: AsyncClient, org_a, readonly_a, readonly_a_token
):
    h = _headers(readonly_a_token, org_a.id)
    r = await client.post("/api/v1/metrics", json={"name": "x"}, headers=h)
    assert r.status_code == 403, r.text


async def test_metric_cross_org_isolated(
    client: AsyncClient,
    org_a,
    analyst_a,
    analyst_a_token,
    org_b,
    analyst_b,
    analyst_b_token,
):
    a_h = _headers(analyst_a_token, org_a.id)
    mid = (await client.post("/api/v1/metrics", json={"name": "m"}, headers=a_h)).json()["id"]

    b_h = _headers(analyst_b_token, org_b.id)
    assert (await client.get("/api/v1/metrics", headers=b_h)).json() == []
    assert (
        await client.patch(f"/api/v1/metrics/{mid}", json={"name": "z"}, headers=b_h)
    ).status_code == 404
    assert (await client.delete(f"/api/v1/metrics/{mid}", headers=b_h)).status_code == 404


# --- Case metric values ---


async def test_case_metric_values_roundtrip(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    mid = (await client.post("/api/v1/metrics", json={"name": "score"}, headers=h)).json()["id"]

    put = await client.put(
        f"/api/v1/cases/{case.id}/metrics",
        json={"metrics": {mid: "42"}},
        headers=h,
    )
    assert put.status_code == 200, put.text
    assert any(v["metric_id"] == mid and v["value"] == "42" for v in put.json())

    got = await client.get(f"/api/v1/cases/{case.id}/metrics", headers=h)
    assert got.status_code == 200
    assert any(v["value"] == "42" for v in got.json())


async def test_case_metric_rejects_foreign_metric(
    client: AsyncClient,
    session,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
    org_b,
    analyst_b,
    analyst_b_token,
):
    """Setting a value for a metric owned by another org must 404."""
    b_h = _headers(analyst_b_token, org_b.id)
    foreign_mid = (
        await client.post("/api/v1/metrics", json={"name": "b-metric"}, headers=b_h)
    ).json()["id"]

    a_h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    r = await client.put(
        f"/api/v1/cases/{case.id}/metrics",
        json={"metrics": {foreign_mid: "1"}},
        headers=a_h,
    )
    assert r.status_code == 404, r.text


# --- Dashboards ---


async def test_dashboard_crud(client: AsyncClient, org_a, analyst_a, analyst_a_token):
    h = _headers(analyst_a_token, org_a.id)
    c = await client.post(
        "/api/v1/dashboards",
        json={"name": "SOC", "layout": {"widgets": []}, "is_public": True},
        headers=h,
    )
    assert c.status_code == 201, c.text
    did = c.json()["id"]
    assert c.json()["is_public"] is True

    assert any(
        d["id"] == did
        for d in (await client.get("/api/v1/dashboards", headers=h)).json()
    )

    upd = await client.patch(
        f"/api/v1/dashboards/{did}", json={"name": "SOC v2"}, headers=h
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "SOC v2"

    assert (await client.delete(f"/api/v1/dashboards/{did}", headers=h)).status_code == 204


async def test_dashboard_readonly_forbidden(
    client: AsyncClient, org_a, readonly_a, readonly_a_token
):
    h = _headers(readonly_a_token, org_a.id)
    r = await client.post("/api/v1/dashboards", json={"name": "x"}, headers=h)
    assert r.status_code == 403, r.text


async def test_dashboard_cross_org_isolated(
    client: AsyncClient,
    org_a,
    analyst_a,
    analyst_a_token,
    org_b,
    analyst_b,
    analyst_b_token,
):
    a_h = _headers(analyst_a_token, org_a.id)
    did = (
        await client.post("/api/v1/dashboards", json={"name": "d"}, headers=a_h)
    ).json()["id"]

    b_h = _headers(analyst_b_token, org_b.id)
    assert (await client.get("/api/v1/dashboards", headers=b_h)).json() == []
    assert (
        await client.patch(f"/api/v1/dashboards/{did}", json={"name": "z"}, headers=b_h)
    ).status_code == 404
    assert (await client.delete(f"/api/v1/dashboards/{did}", headers=b_h)).status_code == 404
