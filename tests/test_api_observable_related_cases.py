"""Tests for the observable drawer's "Related cases"
(GET /observables/{id}/related-cases): cases containing the observable's
value (same type + value), CaseShare-visible, ordered by case id descending."""
from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user, title="c", roles_key="org-admin"):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title),
        owner_org_id=org.id,
        owner_role_id=builtin_roles[roles_key].id,
        created_by=str(user.id),
    )


async def _add_obs(client, headers, case_id, otype, data, **extra):
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": otype, "data": data, **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_related_cases_ordered_by_case_id_desc(
    client, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    b = await _make_case(session, org_a, builtin_roles, analyst_a, "B")
    c = await _make_case(session, org_a, builtin_roles, analyst_a, "C")

    # The same IP appears in all three cases; a different value only in A.
    obs_a = await _add_obs(client, h, a.id, "ip", "8.8.8.8")
    await _add_obs(client, h, a.id, "domain", "only.test")
    await _add_obs(client, h, b.id, "ip", "8.8.8.8")
    await _add_obs(client, h, c.id, "ip", "8.8.8.8")

    r = await client.get(
        f"/api/v1/observables/{obs_a['id']}/related-cases", headers=h
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # Every case holding 8.8.8.8 is returned (including the observable's own
    # case A), newest case id first.
    assert [row["id"] for row in rows] == [c.id, b.id, a.id]
    assert all(row["shared_observables"] == 1 for row in rows)


async def test_related_cases_visibility_scoped(
    client,
    session,
    org_a,
    org_b,
    builtin_roles,
    builtin_roles_b,
    observable_types,
    analyst_a,
    analyst_a_token,
    analyst_b,
    analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    hb = _headers(analyst_b_token, org_b.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    other = await _make_case(session, org_b, builtin_roles_b, analyst_b, "B")
    obs_a = await _add_obs(client, ha, a.id, "ip", "4.4.4.4")
    await _add_obs(client, hb, other.id, "ip", "4.4.4.4")

    # org_a holds no CaseShare on org_b's case, so only its own case is related.
    r = await client.get(
        f"/api/v1/observables/{obs_a['id']}/related-cases", headers=ha
    )
    assert r.status_code == 200, r.text
    assert [row["id"] for row in r.json()] == [a.id]
