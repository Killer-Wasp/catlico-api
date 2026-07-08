from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.task import TaskCreate


async def test_log_crud(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    headers = {
        "Authorization": f"Bearer {analyst_a_token}",
        "X-Organisation-Id": org_a.id,
    }

    base = f"/api/v1/cases/{case.id}/tasks/{task.id}/logs"
    response = await client.post(
        base,
        json={"message": "checked dns logs"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    log = response.json()
    assert log["message"] == "checked dns logs"
    assert log["public_id"] == f"TL-{case.id}-{task.id}-{log['id']}"

    response = await client.get(base, headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert len(response.json()["items"]) == 1

    response = await client.patch(
        f"{base}/{log['id']}",
        json={"message": "updated"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["message"] == "updated"

    response = await client.delete(f"{base}/{log['id']}", headers=headers)
    assert response.status_code == 204


async def test_task_list_reports_live_log_count(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    await session.commit()
    headers = {
        "Authorization": f"Bearer {analyst_a_token}",
        "X-Organisation-Id": org_a.id,
    }
    base = f"/api/v1/cases/{case.id}/tasks/{task.id}/logs"

    async def list_log_count() -> int:
        resp = await client.get(f"/api/v1/cases/{case.id}/tasks", headers=headers)
        assert resp.status_code == 200, resp.text
        return resp.json()["items"][0]["log_count"]

    # No logs yet.
    assert await list_log_count() == 0

    # Two logs → count is 2.
    ids = []
    for msg in ("one", "two"):
        r = await client.post(base, json={"message": msg}, headers=headers)
        assert r.status_code == 201
        ids.append(r.json()["id"])
    assert await list_log_count() == 2

    # Deleting a log decrements the count (a live COUNT, not the monotonic seq).
    assert (await client.delete(f"{base}/{ids[0]}", headers=headers)).status_code == 204
    assert await list_log_count() == 1
