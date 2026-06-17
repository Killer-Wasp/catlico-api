"""Tests for the Tasks/Logs refinement (grill 2026-06-13): status state machine,
auto end_date, soft delete, per-org flag, due_date, log occurred_at, task-groups."""
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.task import TaskCreate


async def _make_case_and_task(session, org, builtin_roles, user, **task_kwargs):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t", **task_kwargs),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(user.id),
    )
    return case, task


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


# --- Status state machine + auto end_date ---


async def test_legal_transition_to_completed_sets_end_date(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)

    # Waiting -> InProgress -> Completed
    r = await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "InProgress"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["end_date"] is None

    r = await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "Completed"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["end_date"] is not None


async def test_illegal_transition_rejected(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    # Waiting -> Completed is not allowed (must pass through InProgress)
    r = await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "Completed"}, headers=h)
    assert r.status_code == 422, r.text


async def test_reopen_clears_end_date(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "InProgress"}, headers=h)
    await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "Completed"}, headers=h)
    r = await client.patch(f"/api/v1/tasks/{task.id}", json={"status": "Waiting"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["end_date"] is None


# --- due_date is independent of end_date ---


async def test_due_date_roundtrips(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    due = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    _, task = await _make_case_and_task(
        session, org_a, builtin_roles, analyst_a, due_date=due
    )
    h = _headers(analyst_a_token, org_a.id)
    r = await client.get(f"/api/v1/tasks/{task.id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["due_date"] is not None
    assert body["end_date"] is None


# --- Soft delete ---


async def test_soft_deleted_task_returns_404_and_hides_logs(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await client.post(f"/api/v1/tasks/{task.id}/logs", json={"message": "m"}, headers=h)

    r = await client.delete(f"/api/v1/tasks/{task.id}", headers=h)
    assert r.status_code == 204

    # Task no longer visible
    assert (await client.get(f"/api/v1/tasks/{task.id}", headers=h)).status_code == 404
    # Its logs cascade-soft-deleted: listing logs 404s (task gone), row still in DB
    from app.models.log import Log
    from sqlmodel import select
    logs = (await session.execute(select(Log).where(Log.task_id == task.id))).scalars().all()
    assert len(logs) == 1
    assert logs[0].deleted_at is not None


# --- Per-org flag ---


async def test_flag_is_per_org(
    client,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    from app.models.case_share import CaseShare
    from app.models.task_share import TaskShare

    case, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    # Share case + task with org-b so it can see them
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    session.add(TaskShare(task_id=task.id, organisation_id=org_b.id, created_by=str(analyst_a.id)))
    await session.commit()

    ha = _headers(analyst_a_token, org_a.id)
    hb = _headers(analyst_b_token, org_b.id)

    # org-a flags the task
    assert (await client.put(f"/api/v1/tasks/{task.id}/flag", headers=ha)).status_code == 204

    # org-a sees flagged=True, org-b sees flagged=False
    assert (await client.get(f"/api/v1/tasks/{task.id}", headers=ha)).json()["flagged"] is True
    assert (await client.get(f"/api/v1/tasks/{task.id}", headers=hb)).json()["flagged"] is False

    # Unflag is idempotent
    assert (await client.delete(f"/api/v1/tasks/{task.id}/flag", headers=ha)).status_code == 204
    assert (await client.get(f"/api/v1/tasks/{task.id}", headers=ha)).json()["flagged"] is False


# --- Assignee must belong to the task's creator org ---


async def test_assignee_must_be_in_creator_org(
    client,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    # analyst_b is NOT a member of org-a (the task's creator org) -> 422
    r = await client.patch(
        f"/api/v1/tasks/{task.id}", json={"assignee_id": str(analyst_b.id)}, headers=h
    )
    assert r.status_code == 422, r.text


# --- Log occurred_at backdating + timeline order ---


async def test_log_occurred_at_orders_timeline(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    _, task = await _make_case_and_task(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    # Create "later-typed but earlier-occurred" log second; it should sort first.
    await client.post(f"/api/v1/tasks/{task.id}/logs", json={"message": "newer-event"}, headers=h)
    backdated = datetime(2020, 1, 1, tzinfo=UTC).isoformat()
    await client.post(
        f"/api/v1/tasks/{task.id}/logs",
        json={"message": "older-event", "occurred_at": backdated},
        headers=h,
    )
    r = await client.get(f"/api/v1/tasks/{task.id}/logs", headers=h)
    msgs = [item["message"] for item in r.json()["items"]]
    assert msgs[0] == "older-event"


# --- Distinct task-groups endpoint ---


async def test_task_groups_endpoint(
    client, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    for grp in ("Triage", "Containment", "Triage", ""):
        await task_crud.create_task(
            session,
            TaskCreate(title="t", group=grp),
            case_id=case.id,
            organisation_id=org_a.id,
            created_by=str(analyst_a.id),
        )
    h = _headers(analyst_a_token, org_a.id)
    r = await client.get(f"/api/v1/cases/{case.id}/task-groups", headers=h)
    assert r.status_code == 200, r.text
    # Distinct, sorted, empty string excluded
    assert r.json() == ["Containment", "Triage"]
