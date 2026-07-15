"""Tests for the case→case "Similar cases" tab (GET /cases/{id}/similar).

Mirrors the alert `similar-cases` behaviour: cases sharing an observable
(type + value), overlap-ranked, `ignore_similarity` respected, CaseShare-visible,
with the source case and merged/duplicated tombstones excluded."""
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import case_status as case_status_crud
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


async def test_overlap_ranking_and_self_exclusion(
    client, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    b = await _make_case(session, org_a, builtin_roles, analyst_a, "B")
    c = await _make_case(session, org_a, builtin_roles, analyst_a, "C")

    # Source case A has two observables; B shares one, C shares both.
    await _add_obs(client, h, a.id, "ip", "1.1.1.1")
    await _add_obs(client, h, a.id, "domain", "evil.test")
    await _add_obs(client, h, b.id, "ip", "1.1.1.1")
    await _add_obs(client, h, c.id, "ip", "1.1.1.1")
    await _add_obs(client, h, c.id, "domain", "evil.test")

    r = await client.get(f"/api/v1/cases/{a.id}/similar", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    # A itself is never in its own results; C (overlap 2) ranks above B (overlap 1).
    assert [row["id"] for row in rows] == [c.id, b.id]
    assert rows[0]["shared_observables"] == 2
    assert rows[1]["shared_observables"] == 1


async def test_ignore_similarity_excluded(
    client, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    b = await _make_case(session, org_a, builtin_roles, analyst_a, "B")

    # The shared observable is flagged ignore_similarity on the source side.
    await _add_obs(client, h, a.id, "ip", "2.2.2.2", ignore_similarity=True)
    await _add_obs(client, h, b.id, "ip", "2.2.2.2")

    r = await client.get(f"/api/v1/cases/{a.id}/similar", headers=h)
    assert r.json() == []


async def test_tombstone_excluded(
    client, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    dup = await _make_case(session, org_a, builtin_roles, analyst_a, "DUP")
    await _add_obs(client, h, a.id, "ip", "3.3.3.3")
    await _add_obs(client, h, dup.id, "ip", "3.3.3.3")

    # A merged/duplicated case is a tombstone and must not surface.
    duplicated = await case_status_crud.duplicated_status(session, org_a.id)
    dup.status_id = duplicated.id
    session.add(dup)
    await session.commit()

    r = await client.get(f"/api/v1/cases/{a.id}/similar", headers=h)
    assert r.json() == []


async def test_share_visibility(
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
    await _add_obs(client, ha, a.id, "ip", "4.4.4.4")
    await _add_obs(client, hb, other.id, "ip", "4.4.4.4")

    # org_a does not hold a CaseShare on org_b's case, so it is not visible.
    r = await client.get(f"/api/v1/cases/{a.id}/similar", headers=ha)
    assert r.json() == []


async def test_similar_count_in_counts(
    client, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    a = await _make_case(session, org_a, builtin_roles, analyst_a, "A")
    b = await _make_case(session, org_a, builtin_roles, analyst_a, "B")
    await _add_obs(client, h, a.id, "ip", "5.5.5.5")
    await _add_obs(client, h, b.id, "ip", "5.5.5.5")

    counts = await client.get(f"/api/v1/cases/{a.id}/counts", headers=h)
    assert counts.json()["similar"] == 1
