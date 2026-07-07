"""Organisation members endpoint — email is joined for member lists / mentions."""

from httpx import AsyncClient


async def test_list_members_includes_email(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    resp = await client.get(
        f"/api/v1/organisations/{org_a.id}/members",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert resp.status_code == 200, resp.text
    members = resp.json()
    assert {m["email"] for m in members} == {analyst_a.email}
    assert members[0]["user_id"] == str(analyst_a.id)
    # Name + avatar flag are joined so clients can render an author avatar
    # without a per-row user lookup.
    assert members[0]["first_name"] == analyst_a.first_name
    assert members[0]["last_name"] == analyst_a.last_name
    assert members[0]["has_avatar"] is False


async def test_org_admin_can_add_member_by_email_and_name(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    resp = await client.post(
        f"/api/v1/organisations/{org_a.id}/members",
        json={
            "email": "ada@test.com",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "role_id": str(builtin_roles["analyst"].id),
        },
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )

    assert resp.status_code == 201, resp.text
    member = resp.json()
    assert member["email"] == "ada@test.com"
    assert member["first_name"] == "Ada"
    assert member["last_name"] == "Lovelace"
    assert member["organisation_id"] == org_a.id
