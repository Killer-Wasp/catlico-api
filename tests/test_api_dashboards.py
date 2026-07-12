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


async def _make_board(client, headers, name="Shared board"):
    return (
        await client.post(
            "/api/v1/dashboards",
            json={"name": name, "layout": _layout()},
            headers=headers,
        )
    ).json()


async def test_share_mint_then_public_view_renders_overview(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = await _make_board(client, h)
    assert board["share_enabled"] is False

    mint = await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    assert mint.status_code == 200, mint.text
    token = mint.json()["token"]
    assert token and len(token) >= 32

    # share_enabled now reflects the active link.
    listed = (await client.get("/api/v1/dashboards", headers=h)).json()
    assert next(d for d in listed if d["id"] == board["id"])["share_enabled"] is True

    # The public view needs NO auth and returns layout + overview only.
    pub = await client.get(f"/api/v1/public/dashboards/{token}")
    assert pub.status_code == 200, pub.text
    body = pub.json()
    assert body["name"] == board["name"]
    assert body["layout"] == _layout()
    assert "overview" in body and "stats" in body["overview"]
    # No tenancy metadata leaks.
    assert "organisation_id" not in body
    assert "created_by" not in body


async def test_public_view_is_unauthenticated(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = await _make_board(client, h)
    token = (
        await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    ).json()["token"]
    # Explicitly no Authorization / X-Organisation-Id headers.
    pub = await client.get(f"/api/v1/public/dashboards/{token}")
    assert pub.status_code == 200


async def test_revoke_invalidates_the_link(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = await _make_board(client, h)
    token = (
        await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    ).json()["token"]
    assert (await client.get(f"/api/v1/public/dashboards/{token}")).status_code == 200

    rev = await client.delete(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    assert rev.status_code == 204
    # Revoked token is now an indistinguishable 404.
    assert (await client.get(f"/api/v1/public/dashboards/{token}")).status_code == 404
    # Revoke is idempotent.
    assert (
        await client.delete(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    ).status_code == 204


async def test_remint_rotates_and_invalidates_previous_token(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    board = await _make_board(client, h)
    first = (
        await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    ).json()["token"]
    second = (
        await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=h)
    ).json()["token"]
    assert first != second
    assert (await client.get(f"/api/v1/public/dashboards/{first}")).status_code == 404
    assert (await client.get(f"/api/v1/public/dashboards/{second}")).status_code == 200


async def test_unknown_token_is_404(client: AsyncClient):
    assert (
        await client.get("/api/v1/public/dashboards/not-a-real-token")
    ).status_code == 404


async def test_only_owner_can_share_or_revoke(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token,
    readonly_a, readonly_a_token,
):
    h = _h(analyst_a_token, org_a.id)
    board = await _make_board(client, h)
    other = _h(readonly_a_token, org_a.id)
    # A non-owner in the same org cannot mint or revoke.
    assert (
        await client.post(f"/api/v1/dashboards/{board['id']}/share", headers=other)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/dashboards/{board['id']}/share", headers=other)
    ).status_code == 403
