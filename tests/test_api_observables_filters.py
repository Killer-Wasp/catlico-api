"""Server-side clause filtering + facets for GET /observables.

Filters are `filter=key~op~value` terms (keys: type, tlp, flag, source, value):
OR within a key, AND across keys."""

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


async def _add_obs(client, h, case_id, **body):
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables", json=body, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


def _f(key, op, value):
    return f"{key}~{op}~{value}"


async def _list(client, h, params=None):
    resp = await client.get("/api/v1/observables/", params=params or {}, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_filter_observables_by_type(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await _add_obs(client, h, case.id, observable_type="ip", data="1.2.3.4")
    await _add_obs(client, h, case.id, observable_type="domain", data="evil.test")

    body = await _list(client, h, {"filter": [_f("type", "eq", "ip")]})
    assert {o["data"] for o in body["items"]} == {"1.2.3.4"}


async def test_filter_observables_by_tlp_and_flag(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await _add_obs(client, h, case.id, observable_type="ip", data="1.1.1.1", tlp=3, ioc=True)
    await _add_obs(client, h, case.id, observable_type="ip", data="2.2.2.2", tlp=1)

    by_tlp = await _list(client, h, {"filter": [_f("tlp", "eq", "3")]})
    assert {o["data"] for o in by_tlp["items"]} == {"1.1.1.1"}

    by_flag = await _list(client, h, {"filter": [_f("flag", "eq", "ioc")]})
    assert {o["data"] for o in by_flag["items"]} == {"1.1.1.1"}


async def test_filter_observables_by_value_contains(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await _add_obs(client, h, case.id, observable_type="domain", data="login.example.com")
    await _add_obs(client, h, case.id, observable_type="domain", data="safe.test")

    body = await _list(client, h, {"filter": [_f("value", "co", "example")]})
    assert {o["data"] for o in body["items"]} == {"login.example.com"}


async def test_filter_observables_by_source(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case_a = await _make_case(session, org_a, builtin_roles, analyst_a)
    case_b = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await _add_obs(client, h, case_a.id, observable_type="ip", data="10.0.0.1")
    await _add_obs(client, h, case_b.id, observable_type="ip", data="10.0.0.2")

    body = await _list(client, h, {"filter": [_f("source", "eq", f"#{case_a.id}")]})
    assert {o["data"] for o in body["items"]} == {"10.0.0.1"}


async def test_observable_facets_sources(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await _add_obs(client, h, case.id, observable_type="ip", data="1.2.3.4")

    resp = await client.get("/api/v1/observables/filters", headers=h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["sources"] == [f"#{case.id}"]
