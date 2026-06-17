from app.crud import case_ as case_crud
from app.crud.case_share import get_share, list_shares
from app.models.case_ import CaseCreate


async def test_create_case_inserts_owner_share(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="ransomware @ HQ"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    assert case.id is not None
    assert case.title == "ransomware @ HQ"
    assert case.severity == 2
    assert case.tlp == 2

    share = await get_share(session, case.id, org_a.id)
    assert share is not None
    assert share.is_owner is True
    assert share.role_id == builtin_roles["org-admin"].id


async def test_list_cases_for_org_filters_by_share(
    session, org_a, org_b, builtin_roles, analyst_a
):
    await case_crud.create_case(
        session,
        CaseCreate(title="a-1"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await case_crud.create_case(
        session,
        CaseCreate(title="b-1"),
        owner_org_id=org_b.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    a_cases, a_total = await case_crud.list_cases_for_org(session, org_a.id)
    b_cases, b_total = await case_crud.list_cases_for_org(session, org_b.id)
    assert {c.title for c in a_cases} == {"a-1"}
    assert {c.title for c in b_cases} == {"b-1"}
    assert a_total == 1
    assert b_total == 1


async def test_list_shares_returns_all(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="x"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    shares = await list_shares(session, case.id)
    assert len(shares) == 1
    assert shares[0].organisation_id == org_a.id
