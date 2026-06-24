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
