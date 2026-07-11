"""Dashboards as user-owned, optionally org-shared views (metrics_dashboards)."""

from httpx import AsyncClient


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _layout():
    return {"widgets": [{"type": "kpi.open_cases", "size": "sm"}]}


async def test_create_is_owned_and_private_by_default(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token, readonly_a, readonly_a_token
):
    h = _h(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/dashboards",
        json={"name": "My board", "layout": _layout()},
        headers=h,
    )
    assert r.status_code == 201, r.text
    board = r.json()
    assert board["is_owner"] is True
    assert board["is_public"] is False
    assert board["created_by"] == str(analyst_a.id)

    # Owner sees it in their list.
    mine = (await client.get("/api/v1/dashboards", headers=h)).json()
    assert board["id"] in {d["id"] for d in mine}

    # A different member of the same org does NOT see a private dashboard.
    other = _h(readonly_a_token, org_a.id)
    others_list = (await client.get("/api/v1/dashboards", headers=other)).json()
    assert board["id"] not in {d["id"] for d in others_list}


async def test_sharing_exposes_read_only_to_org(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token, readonly_a, readonly_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = (
        await client.post(
            "/api/v1/dashboards", json={"name": "Team board", "layout": _layout()}, headers=h
        )
    ).json()

    # Share with the organisation.
    shared = await client.patch(
        f"/api/v1/dashboards/{board['id']}", json={"is_public": True}, headers=h
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["is_public"] is True

    # Now visible to another org member — but read-only (not their dashboard).
    other = _h(readonly_a_token, org_a.id)
    others = (await client.get("/api/v1/dashboards", headers=other)).json()
    seen = {d["id"]: d for d in others}
    assert board["id"] in seen
    assert seen[board["id"]]["is_owner"] is False

    # The non-owner cannot edit or delete it.
    assert (
        await client.patch(
            f"/api/v1/dashboards/{board['id']}", json={"name": "hijack"}, headers=other
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/dashboards/{board['id']}", headers=other)
    ).status_code == 403


async def test_not_visible_across_organisations(
    client: AsyncClient, org_a, org_b, builtin_roles, analyst_a, analyst_a_token, analyst_b, analyst_b_token
):
    h = _h(analyst_a_token, org_a.id)
    board = (
        await client.post(
            "/api/v1/dashboards",
            json={"name": "Org A board", "layout": _layout(), "is_public": True},
            headers=h,
        )
    ).json()

    # Even shared, an org-A dashboard never appears for an org-B user.
    other = _h(analyst_b_token, org_b.id)
    b_list = (await client.get("/api/v1/dashboards", headers=other)).json()
    assert board["id"] not in {d["id"] for d in b_list}


async def test_owner_can_update_and_delete(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = (
        await client.post(
            "/api/v1/dashboards", json={"name": "Draft", "layout": _layout()}, headers=h
        )
    ).json()

    new_layout = {"widgets": [{"type": "chart.cases_by_status", "size": "lg"}]}
    updated = await client.patch(
        f"/api/v1/dashboards/{board['id']}",
        json={"name": "Renamed", "layout": new_layout},
        headers=h,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["layout"] == new_layout

    assert (
        await client.delete(f"/api/v1/dashboards/{board['id']}", headers=h)
    ).status_code == 204
    remaining = (await client.get("/api/v1/dashboards", headers=h)).json()
    assert board["id"] not in {d["id"] for d in remaining}
