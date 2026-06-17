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
        f"/api/v1/tasks/{task.id}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["title"] == "t"

    response = await client.patch(
        f"/api/v1/tasks/{task.id}",
        json={"status": "InProgress"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "InProgress"


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
        f"/api/v1/tasks/{task.id}",
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
    session.add(TaskShare(task_id=task.id, organisation_id=org_b.id, created_by=str(analyst_a.id)))
    await session.commit()

    response = await client.delete(
        f"/api/v1/tasks/{task.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403
