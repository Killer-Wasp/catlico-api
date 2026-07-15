"""Server-side filtering, sorting, pagination and facets for GET /cases.

Filters are sent as repeated `filter=key~op~value` terms: OR within a key,
AND across keys. Keys are status, severity, assignee, title, case, and
`tag:<group-key>` for tag values (free tags are not filterable)."""

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import case_status as case_status_crud
from app.crud import tag as tag_crud
from app.models.case_ import Case, CaseCreate
from app.models.tag import TaggableType


async def _seed_case(
    session,
    org_a,
    builtin_roles,
    created_by,
    *,
    title="case",
    severity=2,
    status="Open",
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
    if status != "Open":
        target = await case_status_crud.get_status_by_label(session, status, org_a.id)
        case.status_id = target.id
        session.add(case)
        await session.flush()
    if tags:
        await tag_crud.set_tags(session, TaggableType.case, str(case.id), tags)
    return case


def _f(key: str, op: str, value: str) -> str:
    """Encode a single `key~op~value` filter term."""
    return f"{key}~{op}~{value}"


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
        status="Resolved",
    )
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="dup-one",
        status="Duplicated",
    )

    # Same key, two values → OR.
    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"filter": [_f("status", "eq", "Open"), _f("status", "eq", "Resolved")]},
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

    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"filter": [_f("severity", "eq", "3"), _f("severity", "eq", "4")]},
    )
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
        client, analyst_a_token, org_a, {"filter": [_f("assignee", "eq", analyst_a.email)]}
    )
    assert {c["title"] for c in only_mine["items"]} == {"mine"}

    only_unassigned = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("assignee", "eq", "Unassigned")]}
    )
    assert {c["title"] for c in only_unassigned["items"]} == {"nobody"}

    both = await _list(
        client,
        analyst_a_token,
        org_a,
        {
            "filter": [
                _f("assignee", "eq", analyst_a.email),
                _f("assignee", "eq", "Unassigned"),
            ]
        },
    )
    assert {c["title"] for c in both["items"]} == {"mine", "nobody"}


async def test_filter_by_tag_equals(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="amber", tags=["tlp:amber"]
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="red", tags=["tlp:red"]
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="untagged")

    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("tag:tlp", "eq", "amber")]}
    )
    assert {c["title"] for c in body["items"]} == {"amber"}


async def test_filter_by_tag_or_within_key(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    # tlp:amber OR tlp:red — same group key → union. (The regression that a
    # namespace:predicate grouping would have broken.)
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="amber", tags=["tlp:amber"]
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="red", tags=["tlp:red"]
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="green", tags=["tlp:green"]
    )

    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"filter": [_f("tag:tlp", "eq", "amber"), _f("tag:tlp", "eq", "red")]},
    )
    assert {c["title"] for c in body["items"]} == {"amber", "red"}


async def test_filter_by_tag_and_across_keys(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    # Different tag keys → intersection.
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="both",
        tags=["tlp:amber", "kill-chain:phase=exploit"],
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="amber-only", tags=["tlp:amber"]
    )
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="phase-only",
        tags=["kill-chain:phase=exploit"],
    )

    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {
            "filter": [
                _f("tag:tlp", "eq", "amber"),
                _f("tag:kill-chain:phase", "eq", "exploit"),
            ]
        },
    )
    assert {c["title"] for c in body["items"]} == {"both"}


async def test_filter_invalid_values_match_nothing(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """Unusable values on known keys (bogus enum member, non-numeric severity)
    must return an empty page, not a 500 — status is a native Postgres enum."""
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="one")

    for term in ("status~eq~bogus", "severity~eq~abc"):
        body = await _list(client, analyst_a_token, org_a, {"filter": [term]})
        assert body["total"] == 0, term


async def test_filter_by_status_contains(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """Contains on the enum status resolves members in Python (ilike doesn't
    compile against a Postgres enum column)."""
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="open-one")
    await _seed_case(
        session,
        org_a,
        builtin_roles,
        analyst_a.id,
        title="resolved-one",
        status="Resolved",
    )

    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("status", "co", "resol")]}
    )
    assert {c["title"] for c in body["items"]} == {"resolved-one"}


async def test_filter_by_title_contains_multi(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="OAuth consent grant"
    )
    await _seed_case(
        session, org_a, builtin_roles, analyst_a.id, title="Ransomware outbreak"
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="quiet day")

    # Two Contains clauses on the same key → OR.
    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"filter": [_f("title", "co", "consent"), _f("title", "co", "ransom")]},
    )
    assert {c["title"] for c in body["items"]} == {
        "OAuth consent grant",
        "Ransomware outbreak",
    }


async def test_filter_by_case_number(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    target = await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="t1")
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="t2")

    # The "#" prefix is tolerated and stripped.
    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("case", "eq", f"#{target.id}")]}
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
        # Two-part (tlp), three-part (kill-chain), and a free tag (excluded).
        tags=["tlp:amber", "tlp:red", "kill-chain:phase=exploit", "phishing"],
    )
    await _seed_case(session, org_a, builtin_roles, analyst_a.id, title="unassigned")

    resp = await client.get(
        "/api/v1/cases/filters", headers=_headers(analyst_a_token, org_a)
    )
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["assignees"] == [analyst_a.email]
    assert facets["unassigned"] is True
    assert facets["tag_keys"] == {
        "tlp": ["amber", "red"],
        "kill-chain:phase": ["exploit"],
    }
    # Free tags are not filterable.
    assert "phishing" not in facets["tag_keys"]


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
        tags=["tlp:amber"],
    )

    resp = await client.get(
        "/api/v1/cases/filters", headers=_headers(analyst_b_token, org_b)
    )
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["assignees"] == []
    assert facets["tag_keys"] == {}
    assert facets["unassigned"] is False
