"""Tests for Case Comments: rides case visibility, author-only edit,
author-or-owner soft-delete."""
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate
from app.models.case_share import CaseShare


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user):
    return await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


async def test_create_list_edit_delete_comment(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/comments", json={"message": "first look"}, headers=h
    )
    assert r.status_code == 201, r.text
    comment_id = r.json()["id"]

    lst = await client.get(f"/api/v1/cases/{case.id}/comments", headers=h)
    assert lst.json()["total"] == 1

    # Author edits
    e = await client.patch(
        f"/api/v1/comments/{comment_id}", json={"message": "edited"}, headers=h
    )
    assert e.status_code == 200
    assert e.json()["message"] == "edited"
    assert e.json()["updated_at"] is not None

    # Author deletes
    assert (await client.delete(f"/api/v1/comments/{comment_id}", headers=h)).status_code == 204
    lst2 = await client.get(f"/api/v1/cases/{case.id}/comments", headers=h)
    assert lst2.json()["total"] == 0


async def test_comment_rides_case_visibility(
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
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    # Share with org-b
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

    ha = _headers(analyst_a_token, org_a.id)
    hb = _headers(analyst_b_token, org_b.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/comments", json={"message": "shared note"}, headers=ha
    )
    comment_id = r.json()["id"]

    # org-b sees it (rides case visibility)
    lst = await client.get(f"/api/v1/cases/{case.id}/comments", headers=hb)
    assert lst.json()["total"] == 1

    # org-b (not author) cannot edit
    e = await client.patch(
        f"/api/v1/comments/{comment_id}", json={"message": "hijack"}, headers=hb
    )
    assert e.status_code == 403

    # But the case owner (org-a) can delete org-... here author is org-a anyway;
    # verify non-author non-owner org-b cannot delete
    d = await client.delete(f"/api/v1/comments/{comment_id}", headers=hb)
    assert d.status_code == 403


async def test_case_owner_can_delete_others_comment(
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
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
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
    hb = _headers(analyst_b_token, org_b.id)
    ha = _headers(analyst_a_token, org_a.id)
    # org-b authors a comment
    r = await client.post(
        f"/api/v1/cases/{case.id}/comments", json={"message": "b note"}, headers=hb
    )
    comment_id = r.json()["id"]
    # org-a (case owner) can delete it
    assert (await client.delete(f"/api/v1/comments/{comment_id}", headers=ha)).status_code == 204
