"""Phase 4 §4.2 — multi-assignee (primary + collaborators) for cases + tasks.

Covers: collaborator CRUD via `PUT …/assignees`, read-model `assignees` shape
(primary flagged), membership validation, filter semantics (primary OR
collaborator; UNASSIGNED = neither), facet unions, and the notification fan-out
to *newly-added* collaborators only.
"""

from unittest.mock import AsyncMock

from httpx import AsyncClient
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.audit import AuditOutbox
from app.models.case_ import Case, CaseCreate
from app.models.notification import UserNotification
from app.models.task import TaskCreate
from app.services.outbox_events import notify_feed_consumer


def _headers(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def _make_case(session, org, roles, actor, *, assignee_id=None):
    return await case_crud.create_case(
        session,
        CaseCreate(title="c", assignee_id=assignee_id),
        owner_org_id=org.id,
        owner_role_id=roles["org-admin"].id,
        created_by=str(actor),
    )


# --- Case collaborator CRUD --------------------------------------------------


async def test_case_collaborator_replace_and_read(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)

    resp = await client.put(
        f"/api/v1/cases/{case.id}/assignees",
        json={"user_ids": [str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert resp.status_code == 200, resp.text
    by_id = {a["id"]: a for a in resp.json()["assignees"]}
    assert by_id[str(analyst_a.id)]["is_primary"] is True
    assert by_id[str(analyst_a.id)]["email"] == analyst_a.email
    assert by_id[str(readonly_a.id)]["is_primary"] is False
    assert len(by_id) == 2

    # GET reflects the persisted set.
    got = await client.get(
        f"/api/v1/cases/{case.id}", headers=_headers(analyst_a_token, org_a)
    )
    assert {a["id"] for a in got.json()["assignees"]} == {
        str(analyst_a.id),
        str(readonly_a.id),
    }

    # Replace with the empty set → only the primary remains.
    resp = await client.put(
        f"/api/v1/cases/{case.id}/assignees",
        json={"user_ids": []},
        headers=_headers(analyst_a_token, org_a),
    )
    assert [a["id"] for a in resp.json()["assignees"]] == [str(analyst_a.id)]


async def test_case_collaborator_primary_not_duplicated(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    """Passing the primary inside the collaborator set must not duplicate it."""
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)
    resp = await client.put(
        f"/api/v1/cases/{case.id}/assignees",
        json={"user_ids": [str(analyst_a.id), str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )
    ids = [a["id"] for a in resp.json()["assignees"]]
    assert ids.count(str(analyst_a.id)) == 1
    assert sorted(ids) == sorted([str(analyst_a.id), str(readonly_a.id)])


async def test_case_collaborator_must_be_member(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    builtin_roles_b,
    analyst_a,
    analyst_b,
    analyst_a_token,
):
    """A collaborator who is not a member of the owner org → 422."""
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)
    resp = await client.put(
        f"/api/v1/cases/{case.id}/assignees",
        json={"user_ids": [str(analyst_b.id)]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert resp.status_code == 422, resp.text


# --- Task collaborator CRUD --------------------------------------------------


async def test_task_collaborator_replace_and_read(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t", assignee_id=analyst_a.id),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )

    resp = await client.put(
        f"/api/v1/cases/{case.id}/tasks/{task.id}/assignees",
        json={"user_ids": [str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert resp.status_code == 200, resp.text
    by_id = {a["id"]: a for a in resp.json()["assignees"]}
    assert by_id[str(analyst_a.id)]["is_primary"] is True
    assert by_id[str(readonly_a.id)]["is_primary"] is False

    got = await client.get(
        f"/api/v1/cases/{case.id}/tasks/{task.id}",
        headers=_headers(analyst_a_token, org_a),
    )
    assert {a["id"] for a in got.json()["assignees"]} == {
        str(analyst_a.id),
        str(readonly_a.id),
    }


async def test_task_collaborator_must_be_member(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    builtin_roles_b,
    analyst_a,
    analyst_b,
    analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    resp = await client.put(
        f"/api/v1/cases/{case.id}/tasks/{task.id}/assignees",
        json={"user_ids": [str(analyst_b.id)]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert resp.status_code == 422, resp.text


# --- Filter semantics --------------------------------------------------------


async def _list_cases(client, token, org, params):
    resp = await client.get(
        "/api/v1/cases/", params=params, headers=_headers(token, org)
    )
    assert resp.status_code == 200, resp.text
    return {c["title"] for c in resp.json()["items"]}


async def test_case_filter_matches_collaborator_and_unassigned(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    primary = await _make_case(session, org_a, builtin_roles, analyst_a.id, assignee_id=analyst_a.id)
    primary.title = "primary-case"
    session.add(primary)
    collab = await _make_case(session, org_a, builtin_roles, analyst_a.id)
    collab.title = "collab-case"
    session.add(collab)
    empty = await _make_case(session, org_a, builtin_roles, analyst_a.id)
    empty.title = "empty-case"
    session.add(empty)
    await session.flush()

    # readonly_a is a collaborator (not primary) on collab-case.
    await client.put(
        f"/api/v1/cases/{collab.id}/assignees",
        json={"user_ids": [str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )

    # assignee filter by readonly_a's email → matches via the collaborator join.
    assert await _list_cases(
        client, analyst_a_token, org_a, {"filter": [f"assignee~eq~{readonly_a.email}"]}
    ) == {"collab-case"}

    # assignee filter by analyst_a → the primary-owned case (analyst_a is primary
    # on primary-case; on collab/empty analyst_a is neither).
    assert await _list_cases(
        client, analyst_a_token, org_a, {"filter": [f"assignee~eq~{analyst_a.email}"]}
    ) == {"primary-case"}

    # UNASSIGNED = no primary AND no collaborators → only empty-case.
    assert await _list_cases(
        client, analyst_a_token, org_a, {"filter": ["assignee~eq~Unassigned"]}
    ) == {"empty-case"}


async def test_case_facets_union_collaborators(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    collab = await _make_case(session, org_a, builtin_roles, analyst_a.id)
    await _make_case(session, org_a, builtin_roles, analyst_a.id)  # unassigned
    await session.flush()
    await client.put(
        f"/api/v1/cases/{collab.id}/assignees",
        json={"user_ids": [str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )

    resp = await client.get(
        "/api/v1/cases/filters", headers=_headers(analyst_a_token, org_a)
    )
    facets = resp.json()
    assert readonly_a.email in facets["assignees"]  # collaborator surfaced
    assert facets["unassigned"] is True  # the truly-empty case


async def test_task_filter_matches_collaborator(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a.id)
    task = await task_crud.create_task(
        session,
        TaskCreate(title="collab-task"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    other = await task_crud.create_task(
        session,
        TaskCreate(title="empty-task"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    await client.put(
        f"/api/v1/cases/{case.id}/tasks/{task.id}/assignees",
        json={"user_ids": [str(readonly_a.id)]},
        headers=_headers(analyst_a_token, org_a),
    )

    resp = await client.get(
        "/api/v1/task-queue",
        params={"filter": [f"assignee~eq~{readonly_a.email}"]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert resp.status_code == 200, resp.text
    assert {t["title"] for t in resp.json()["items"]} == {"collab-task"}

    # UNASSIGNED excludes the task that has a collaborator.
    resp = await client.get(
        "/api/v1/task-queue",
        params={"filter": ["assignee~eq~Unassigned"]},
        headers=_headers(analyst_a_token, org_a),
    )
    assert {t["title"] for t in resp.json()["items"]} == {"empty-task"}


# --- Notification fan-out (consumer level) -----------------------------------


async def _case_update_outbox(session, *, org_id, details) -> AuditOutbox:
    case = Case(title="x", created_by="system")
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="admin@test.com",
        details=details,
        organisation_id=org_id,
    )
    await session.flush()
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    return result.scalars().one()


async def test_added_collaborators_fan_out(
    session, org_a, analyst_a, readonly_a, monkeypatch
):
    """`added_assignee_ids` → one targeted `case.assigned` per added collaborator,
    plus the single unchanged org-wide row."""
    row = await _case_update_outbox(
        session,
        org_id=org_a.id,
        details={"added_assignee_ids": [str(analyst_a.id), str(readonly_a.id)]},
    )
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    notifs = (
        await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == org_a.id
            )
        )
    ).scalars().all()
    org_wide = [n for n in notifs if n.user_id is None]
    targeted = [n for n in notifs if n.user_id is not None]
    assert len(org_wide) == 1
    assert {n.user_id for n in targeted} == {analyst_a.id, readonly_a.id}
    assert all(n.event_type == "case.assigned" for n in targeted)


async def test_no_additions_no_fan_out(session, org_a, monkeypatch):
    """An empty `added_assignee_ids` (e.g. a pure removal) → only the org-wide row."""
    row = await _case_update_outbox(
        session, org_id=org_a.id, details={"added_assignee_ids": []}
    )
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = (
        await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == org_a.id
            )
        )
    ).scalars().all()
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_pub.assert_not_called()


async def test_primary_and_collaborator_same_user_deduped(
    session, org_a, analyst_a, monkeypatch
):
    """A user who is both the newly-set primary and in `added_assignee_ids` for the
    same event gets exactly one targeted row (the (outbox_id, user_id) index)."""
    row = await _case_update_outbox(
        session,
        org_id=org_a.id,
        details={
            "assignee_id": [None, str(analyst_a.id)],
            "added_assignee_ids": [str(analyst_a.id)],
        },
    )
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    targeted = (
        await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == org_a.id,
                UserNotification.user_id == analyst_a.id,
            )
        )
    ).scalars().all()
    assert len(targeted) == 1
