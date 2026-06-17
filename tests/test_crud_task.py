from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.case_share import CaseShare
from app.models.organisation_link import (
    AutoShareMode,
    CaseSharingMode,
    OrganisationLink,
)
from app.models.task import TaskCreate
from app.models.task_share import TaskShare


async def test_create_task_no_autoshare(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="case"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="contain host"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    assert task.case_id == case.id
    assert task.organisation_id == org_a.id


async def test_create_task_autoshare_fans_out(
    session, org_a, org_b, builtin_roles, analyst_a
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="shared"),
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

    task = await task_crud.create_task(
        session,
        TaskCreate(title="triage"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    ts = await session.get(TaskShare, (task.id, org_b.id))
    assert ts is not None
