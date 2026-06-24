from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import Case, CaseCreate
from app.models.case_share import CaseShare
from app.models.task import Task, TaskCreate
from app.models.organisation_link import (
    AutoShareMode,
    CaseSharingMode,
    OrganisationLink,
)


async def test_create_and_get_case(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    response = await client.post(
        "/api/v1/cases/",
        json={"title": "phishing report", "severity": 3},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["title"] == "phishing report"
    assert case["severity"] == 3
    assert isinstance(case["id"], int)

    got = await client.get(
        f"/api/v1/cases/{case['id']}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert got.status_code == 200
    assert got.json()["title"] == "phishing report"


async def test_list_cases_only_returns_shared(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_b_token,
):
    # Create cases owned by org-a
    await case_crud.create_case(
        session,
        CaseCreate(title="a-only"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await case_crud.create_case(
        session,
        CaseCreate(title="b-only"),
        owner_org_id=org_b.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_b.id),
    )
    response = await client.get(
        "/api/v1/cases/",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 200
    body = response.json()
    titles = [c["title"] for c in body["items"]]
    assert titles == ["b-only"]
    assert body["total"] == 1


async def test_get_case_not_shared_returns_404(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="secret"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    response = await client.get(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 404


async def test_create_case_requires_write_case(
    client: AsyncClient, org_a, builtin_roles, readonly_a, readonly_a_token
):
    response = await client.post(
        "/api/v1/cases/",
        json={"title": "no"},
        headers={
            "Authorization": f"Bearer {readonly_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 403


async def test_missing_org_header_400(client: AsyncClient, analyst_a_token):
    response = await client.get(
        "/api/v1/cases/",
        headers={"Authorization": f"Bearer {analyst_a_token}"},
    )
    assert response.status_code == 400


async def test_delete_case_owner_only(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="del"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    # Share with org-b as analyst (write:case included via builtin)
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

    # b cannot delete
    response = await client.delete(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403

    # a can delete
    response = await client.delete(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 204


async def test_delete_case_soft_deletes_and_cascades(
    client: AsyncClient,
    session,
    engine,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="soft"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t1"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    await session.commit()

    headers = {
        "Authorization": f"Bearer {analyst_a_token}",
        "X-Organisation-Id": org_a.id,
    }
    response = await client.delete(f"/api/v1/cases/{case.id}", headers=headers)
    assert response.status_code == 204

    # Read paths treat it as gone...
    assert (await client.get(f"/api/v1/cases/{case.id}", headers=headers)).status_code == 404
    listed = await client.get("/api/v1/cases/", headers=headers)
    assert all(c["id"] != case.id for c in listed.json()["items"])

    # ...but the rows survive, flagged, and the child task is cascaded. Use a
    # fresh session so we read committed DB state, not the request's identity map.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        db_case = await verify.get(Case, case.id)
        assert db_case is not None and db_case.deleted_at is not None
        db_task = await verify.get(Task, (task.case_id, task.id))
        assert db_task is not None and db_task.deleted_at is not None


async def test_change_tlp_owner_only(
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
        CaseCreate(title="tlp"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
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

    response = await client.patch(
        f"/api/v1/cases/{case.id}",
        json={"tlp": 3},
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403


async def test_multiorg_autoshare_task_visible(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="multiorg"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    session.add(
        OrganisationLink(
            from_org_id=org_a.id,
            to_org_id=org_b.id,
            case_sharing=CaseSharingMode.manual,
            task_sharing=AutoShareMode.auto_share,
            observable_sharing=AutoShareMode.manual,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()

    # Create task as org-a
    response = await client.post(
        f"/api/v1/cases/{case.id}/tasks",
        json={"title": "investigate"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["public_id"] == f"T-{case.id}-1"

    # org-b should see the task in the case's task list
    response = await client.get(
        f"/api/v1/cases/{case.id}/tasks",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 200
    items = response.json()["items"]
    titles = [t["title"] for t in items]
    assert titles == ["investigate"]
    assert items[0]["public_id"] == f"T-{case.id}-1"
