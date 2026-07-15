from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.case_share import CaseShare
from app.models.task import TaskCreate


async def test_get_and_patch_task(
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
    response = await client.get(
        f"/api/v1/cases/{case.id}/tasks/{task.id}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["title"] == "t"

    response = await client.patch(
        f"/api/v1/cases/{case.id}/tasks/{task.id}",
        json={"status": "InProgress"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "InProgress"


async def test_list_tasks_returns_org_visible_queue_context(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="OAuth consent grant", severity=3),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="Revoke refresh tokens", assignee_id=analyst_a.id),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )

    response = await client.get(
        "/api/v1/task-queue?limit=50",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0] == {
        "id": task.id,
        "public_id": task.public_id,
        "case_id": case.id,
        "organisation_id": org_a.id,
        "title": "Revoke refresh tokens",
        "group": "",
        "description": "",
        "status": "Waiting",
        "assignee_id": str(analyst_a.id),
        "assignees": [
            {
                "id": str(analyst_a.id),
                "email": "analyst-a@test.com",
                "is_primary": True,
            }
        ],
        "order": 0,
        "flagged": False,
        "start_date": None,
        "due_date": None,
        "end_date": None,
        "log_count": 0,
        "created_at": task.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": None,
        "case_title": "OAuth consent grant",
        "case_severity": 3,
        "assignee_email": "analyst-a@test.com",
    }


async def test_task_not_visible_returns_404(
    client: AsyncClient, session, org_a, org_b, builtin_roles, analyst_a, analyst_b_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="hidden"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="hidden-task"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    response = await client.get(
        f"/api/v1/cases/{case.id}/tasks/{task.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 404


async def test_non_creator_non_owner_cannot_delete_task(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="x"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    # share with org-b
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()
    # task created by org-a; org-b has visibility via task_share (we'll add)
    task = await task_crud.create_task(
        session,
        TaskCreate(title="a-task"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    from app.models.task_share import TaskShare
    session.add(TaskShare(case_id=case.id, task_id=task.id, organisation_id=org_b.id, created_by=str(analyst_a.id)))
    await session.commit()

    response = await client.delete(
        f"/api/v1/cases/{case.id}/tasks/{task.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403
