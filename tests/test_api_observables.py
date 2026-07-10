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


async def test_global_list_spans_cases_and_alerts(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    # A case observable...
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": "8.8.8.8"},
        headers=h,
    )
    # ...and an alert observable, both owned by org_a.
    alert = await client.post(
        "/api/v1/alerts/",
        json={"type": "phishing", "source": "gw", "source_ref": "g1", "title": "a"},
        headers=h,
    )
    await client.post(
        f"/api/v1/alerts/{alert.json()['id']}/observables",
        json={"observable_type": "domain", "data": "evil.global"},
        headers=h,
    )

    r = await client.get("/api/v1/observables/", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {o["data"] for o in body["items"]} == {"8.8.8.8", "evil.global"}


async def test_global_list_is_tenant_isolated(
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
    await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": "1.1.1.1"},
        headers=_headers(analyst_a_token, org_a.id),
    )
    # org_b shares nothing on this case, so its global list is empty.
    r = await client.get("/api/v1/observables/", headers=_headers(analyst_b_token, org_b.id))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0


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


async def test_superadmin_can_create_and_delete_observable_type(
    client: AsyncClient, admin_token
):
    headers = {"Authorization": f"Bearer {admin_token}"}

    created = await client.post(
        "/api/v1/observable-types/",
        json={"name": "x-test-agent", "is_attachment": False},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json() == {"name": "x-test-agent", "is_attachment": False}

    deleted = await client.delete(
        "/api/v1/observable-types/x-test-agent",
        headers=headers,
    )
    assert deleted.status_code == 204, deleted.text


# --- Manual create: check-then-insert TOCTOU race ---


async def test_create_case_observable_dedup_race_resolves_as_conflict(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token, monkeypatch,
):
    """The manual create route pre-checks with find_case_observable then inserts:
    a concurrent create of the same (case, type, data) can land between the two,
    and uq_observable_case_dedup rejects the loser with an IntegrityError. Simulate
    it deterministically by making the pre-check report the row absent while it in
    fact exists. The loser must get the route's established 409 "already exists"
    outcome (not a 500), and exactly one row must remain.

    Without the begin_nested savepoint this test fails: the IntegrityError aborts
    the transaction, so the post-collision re-check SELECT itself errors with
    PendingRollbackError instead of returning a clean 409 (confirmed by removing
    the savepoint)."""
    from app.crud import observable as obs_crud

    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    body = {"observable_type": "ip", "data": "5.5.5.5"}

    # Pre-seed the row so the second POST's insert collides for real.
    assert (
        await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)
    ).status_code == 201

    # First find (the route pre-check) lies "absent"; later finds (the
    # post-IntegrityError re-check) see the truth.
    real_find = obs_crud.find_case_observable
    calls = {"n": 0}

    async def racy_find(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(obs_crud, "find_case_observable", racy_find)

    r = await client.post(f"/api/v1/cases/{case.id}/observables", json=body, headers=h)
    assert r.status_code == 409, r.text  # the loser's benign "already exists"

    monkeypatch.undo()
    lst = await client.get(f"/api/v1/cases/{case.id}/observables", headers=h)
    matches = [o for o in lst.json()["items"] if o["data"] == "5.5.5.5"]
    assert len(matches) == 1


async def test_create_case_observable_non_dedup_integrity_not_swallowed(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token, monkeypatch,
):
    """The race handler must not blanket-swallow every IntegrityError as "already
    exists". If the observable still doesn't exist on re-check, the violation came
    from a different constraint and must surface, not be masked as a 409."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.crud import observable as obs_crud

    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)

    async def boom_create(*args, **kwargs):
        raise IntegrityError("INSERT ...", {}, Exception("some other constraint"))

    monkeypatch.setattr(obs_crud, "create_case_observable", boom_create)

    # Re-check finds nothing (nothing was inserted), so it is not a benign
    # duplicate: the IntegrityError re-raises rather than being turned into a 409.
    with pytest.raises(IntegrityError):
        await client.post(
            f"/api/v1/cases/{case.id}/observables",
            json={"observable_type": "ip", "data": "7.7.7.7"},
            headers=h,
        )
