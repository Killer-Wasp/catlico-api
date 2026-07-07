"""Server-side clause filtering + facets for GET /task-queue.

Filters are `filter=key~op~value` terms (keys: status, assignee, kind, case,
title): OR within a key, AND across keys."""

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.task import TaskCreate, TaskStatus


async def _seed_case(session, org_a, builtin_roles, created_by, *, title="c"):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(created_by),
    )


async def _seed_task(
    session,
    org_a,
    created_by,
    case_id,
    *,
    title="t",
    group="",
    assignee_id=None,
    status=TaskStatus.waiting,
):
    task = await task_crud.create_task(
        session,
        TaskCreate(title=title, group=group, assignee_id=assignee_id),
        case_id=case_id,
        organisation_id=org_a.id,
        created_by=str(created_by),
    )
    if status is not TaskStatus.waiting:
        task.status = status
        session.add(task)
        await session.flush()
    return task


def _f(key, op, value):
    return f"{key}~{op}~{value}"


def _headers(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def _list(client, token, org, params=None):
    resp = await client.get(
        "/api/v1/task-queue", params=params or {}, headers=_headers(token, org)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_filter_tasks_by_status(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(session, org_a, analyst_a.id, case.id, title="waiting-one")
    await _seed_task(
        session,
        org_a,
        analyst_a.id,
        case.id,
        title="done-one",
        status=TaskStatus.completed,
    )

    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("status", "eq", "Completed")]}
    )
    assert {t["title"] for t in body["items"]} == {"done-one"}


async def test_filter_tasks_by_kind_including_general(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(session, org_a, analyst_a.id, case.id, title="ungrouped", group="")
    await _seed_task(
        session, org_a, analyst_a.id, case.id, title="triaged", group="triage"
    )

    # "General" is the UI label for the empty group.
    general = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("kind", "eq", "General")]}
    )
    assert {t["title"] for t in general["items"]} == {"ungrouped"}

    triage = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("kind", "eq", "triage")]}
    )
    assert {t["title"] for t in triage["items"]} == {"triaged"}


async def test_filter_tasks_by_assignee_and_unassigned(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(
        session, org_a, analyst_a.id, case.id, title="mine", assignee_id=analyst_a.id
    )
    await _seed_task(session, org_a, analyst_a.id, case.id, title="nobody")

    mine = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("assignee", "eq", analyst_a.email)]}
    )
    assert {t["title"] for t in mine["items"]} == {"mine"}

    nobody = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("assignee", "eq", "Unassigned")]}
    )
    assert {t["title"] for t in nobody["items"]} == {"nobody"}


async def test_filter_tasks_by_title_contains(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(session, org_a, analyst_a.id, case.id, title="Revoke refresh tokens")
    await _seed_task(session, org_a, analyst_a.id, case.id, title="Block sender")

    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("title", "co", "revoke")]}
    )
    assert {t["title"] for t in body["items"]} == {"Revoke refresh tokens"}


async def test_filter_tasks_invalid_status_matches_nothing(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """A bogus enum value must return an empty page, not a 500."""
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(session, org_a, analyst_a.id, case.id, title="t")

    body = await _list(
        client, analyst_a_token, org_a, {"filter": [_f("status", "eq", "bogus")]}
    )
    assert body["total"] == 0


async def test_task_facets(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _seed_case(session, org_a, builtin_roles, analyst_a.id)
    await _seed_task(
        session,
        org_a,
        analyst_a.id,
        case.id,
        title="mine",
        group="triage",
        assignee_id=analyst_a.id,
    )
    await _seed_task(session, org_a, analyst_a.id, case.id, title="ungrouped", group="")

    resp = await client.get(
        "/api/v1/task-queue/filters", headers=_headers(analyst_a_token, org_a)
    )
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["assignees"] == [analyst_a.email]
    assert facets["unassigned"] is True
    assert facets["kinds"] == ["General", "triage"]
