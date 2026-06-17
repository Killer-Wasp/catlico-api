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

    response = await client.post(
        f"/api/v1/tasks/{task.id}/logs",
        json={"message": "checked dns logs"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    log = response.json()
    assert log["message"] == "checked dns logs"

    response = await client.get(f"/api/v1/tasks/{task.id}/logs", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert len(response.json()["items"]) == 1

    response = await client.patch(
        f"/api/v1/logs/{log['id']}",
        json={"message": "updated"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["message"] == "updated"

    response = await client.delete(f"/api/v1/logs/{log['id']}", headers=headers)
    assert response.status_code == 204
