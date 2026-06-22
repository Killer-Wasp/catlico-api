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
