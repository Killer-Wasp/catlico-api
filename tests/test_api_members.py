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
