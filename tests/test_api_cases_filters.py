"""Server-side filtering, sorting, pagination and facets for GET /cases."""

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import tag as tag_crud
from app.models.case_ import Case, CaseCreate, CaseStatus
from app.models.tag import TaggableType


async def _seed_case(
    session,
    org_a,
    builtin_roles,
    created_by,
    *,
    title="case",
    severity=2,
    status=CaseStatus.open,
    assignee_id=None,
    tags=None,
) -> Case:
    case = await case_crud.create_case(
        session,
        CaseCreate(title=title, severity=severity, assignee_id=assignee_id),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(created_by),
    )
    if status is not CaseStatus.open:
        case.status = status
        session.add(case)
        await session.flush()
    if tags:
        await tag_crud.set_tags(session, TaggableType.case, str(case.id), tags)
    return case


def _headers(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def _list(client, token, org, params=None):
    resp = await client.get(
        "/api/v1/cases/", params=params or {}, headers=_headers(token, org)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_filter_by_status_multi(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="open-one")
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="resolved-one",
        status=CaseStatus.resolved,
    )
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="dup-one",
        status=CaseStatus.duplicated,
    )

    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"status_filter": ["Open", "Resolved"]},
    )
    titles = {c["title"] for c in body["items"]}
    assert titles == {"open-one", "resolved-one"}
    assert body["total"] == 2


async def test_filter_by_severity_multi(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    for sev in (1, 2, 3, 4):
        await _seed_case(
            session, org_a, builtin_roles, analyst_a.id, title=f"sev{sev}", severity=sev
        )

    body = await _list(client, analyst_a_token, org_a, {"severity": [3, 4]})
    assert {c["title"] for c in body["items"]} == {"sev3", "sev4"}


async def test_filter_by_assignee_email_and_unassigned(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="mine",
        assignee_id=analyst_a.id,
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="nobody")

    only_mine = await _list(
        client, analyst_a_token, org_a, {"assignee": [analyst_a.email]}
    )
    assert {c["title"] for c in only_mine["items"]} == {"mine"}

    only_unassigned = await _list(
        client, analyst_a_token, org_a, {"assignee": ["Unassigned"]}
    )
    assert {c["title"] for c in only_unassigned["items"]} == {"nobody"}

    both = await _list(
        client,
        analyst_a_token,
        org_a,
        {"assignee": [analyst_a.email, "Unassigned"]},
    )
    assert {c["title"] for c in both["items"]} == {"mine", "nobody"}


async def test_filter_by_tag(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="phish", tags=["phishing"]
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="malware", tags=["malware"]
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="untagged")

    body = await _list(client, analyst_a_token, org_a, {"tag": ["phishing"]})
    assert {c["title"] for c in body["items"]} == {"phish"}


async def test_filter_by_title_substring(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="OAuth consent grant"
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="Ransomware outbreak"
    )

    body = await _list(client, analyst_a_token, org_a, {"title": ["consent"]})
    assert {c["title"] for c in body["items"]} == {"OAuth consent grant"}


async def test_filter_by_case_number(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    target = await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="t1")
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="t2")

    # The "#" prefix is tolerated and stripped.
    body = await _list(
        client, analyst_a_token, org_a, {"case_q": [f"#{target.id}"]}
    )
    assert [c["id"] for c in body["items"]] == [target.id]


async def test_sort_and_pagination(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    ids = [
        (await _seed_case(session, org_a, builtin_roles, analyst_a.id, title=f"c{i}")).id
        for i in range(5)
    ]

    asc = await _list(
        client, analyst_a_token, org_a, {"sort": "id", "order": "asc"}
    )
    assert [c["id"] for c in asc["items"]] == sorted(ids)

    desc = await _list(
        client, analyst_a_token, org_a, {"sort": "id", "order": "desc"}
    )
    assert [c["id"] for c in desc["items"]] == sorted(ids, reverse=True)

    page = await _list(
        client,
        analyst_a_token,
        org_a,
        {"sort": "id", "order": "asc", "skip": 2, "limit": 2},
    )
    assert page["total"] == 5
    assert [c["id"] for c in page["items"]] == sorted(ids)[2:4]


async def test_facets(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="assigned",
        assignee_id=analyst_a.id,
        tags=["phishing", "malware"],
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="unassigned")

    resp = await client.get(
        "/api/v1/cases/filters", headers=_headers(analyst_a_token, org_a)
    )
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["assignees"] == [analyst_a.email]
    assert facets["unassigned"] is True
    assert set(facets["tags"]) == {"phishing", "malware"}


async def test_facets_isolated_per_org(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_b_token,
):
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="a-case",
        assignee_id=analyst_a.id,
        tags=["phishing"],
    )

    resp = await client.get(
        "/api/v1/cases/filters", headers=_headers(analyst_b_token, org_b)
    )
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["assignees"] == []
    assert facets["tags"] == []
    assert facets["unassigned"] is False
