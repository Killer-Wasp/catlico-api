"""CRUD + protection rules for the org-scoped case-status lookup, plus the
stage-based consumers (built-in seeding, status picker on a case, in-use delete
block). Alerts keep the native enum and are untouched."""

from httpx import AsyncClient


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _create_case(client, headers, title="c"):
    r = await client.post("/api/v1/cases/", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_builtins_seeded_per_org(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    r = await client.get("/api/v1/case-statuses/", headers=_h(analyst_a_token, org_a.id))
    assert r.status_code == 200, r.text
    rows = r.json()
    by_label = {s["label"]: s for s in rows}
    assert set(by_label) == {"Open", "In progress", "Resolved", "Duplicated"}
    assert by_label["Open"]["stage"] == "open"
    assert by_label["In progress"]["stage"] == "in_progress"
    assert by_label["Resolved"]["stage"] == "closed"
    assert by_label["Duplicated"]["stage"] == "duplicated"
    assert all(s["is_builtin"] for s in rows)


async def test_new_case_gets_builtin_open(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    case = await _create_case(client, _h(analyst_a_token, org_a.id))
    assert case["status"]["label"] == "Open"
    assert case["status"]["stage"] == "open"
    assert case["status"]["color"]


async def test_create_requires_admin(
    client: AsyncClient, org_a, builtin_roles, readonly_a, readonly_a_token
):
    r = await client.post(
        "/api/v1/case-statuses/",
        json={"label": "Triaging", "stage": "in_progress", "color": "#123456"},
        headers=_h(readonly_a_token, org_a.id),
    )
    assert r.status_code == 403, r.text


async def test_create_and_use_custom_status(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/case-statuses/",
        json={"label": "Triaging", "stage": "in_progress", "color": "#abcdef"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    status_id = created.json()["id"]
    assert created.json()["is_builtin"] is False

    # Duplicate label -> 409.
    dup = await client.post(
        "/api/v1/case-statuses/",
        json={"label": "Triaging", "stage": "open"},
        headers=h,
    )
    assert dup.status_code == 409, dup.text

    # Move a case onto the custom status via PATCH.
    case = await _create_case(client, h)
    patched = await client.patch(
        f"/api/v1/cases/{case['id']}", json={"status_id": status_id}, headers=h
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"]["label"] == "Triaging"
    assert patched.json()["status"]["stage"] == "in_progress"


async def test_delete_blocked_while_in_use_then_allowed(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/case-statuses/",
        json={"label": "Pending", "stage": "open"},
        headers=h,
    )
    status_id = created.json()["id"]
    case = await _create_case(client, h)
    await client.patch(
        f"/api/v1/cases/{case['id']}", json={"status_id": status_id}, headers=h
    )

    # In use -> RESTRICT: 409, offering hide instead.
    blocked = await client.delete(f"/api/v1/case-statuses/{status_id}", headers=h)
    assert blocked.status_code == 409, blocked.text
    assert "hide" in blocked.json()["detail"].lower()

    # Move the case off it, then delete succeeds.
    open_status = next(
        s
        for s in (await client.get("/api/v1/case-statuses/", headers=h)).json()
        if s["label"] == "Open"
    )
    await client.patch(
        f"/api/v1/cases/{case['id']}", json={"status_id": open_status["id"]}, headers=h
    )
    ok = await client.delete(f"/api/v1/case-statuses/{status_id}", headers=h)
    assert ok.status_code == 204, ok.text


async def test_builtin_protected_from_delete_and_relabel(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    rows = (await client.get("/api/v1/case-statuses/", headers=h)).json()
    open_status = next(s for s in rows if s["label"] == "Open")

    # Deleting a built-in -> 409.
    d = await client.delete(f"/api/v1/case-statuses/{open_status['id']}", headers=h)
    assert d.status_code == 409, d.text

    # Relabel / restage a built-in -> 409.
    relabel = await client.patch(
        f"/api/v1/case-statuses/{open_status['id']}",
        json={"label": "Opened"},
        headers=h,
    )
    assert relabel.status_code == 409, relabel.text

    # Cosmetic edits (colour, hidden, position) are allowed on a built-in.
    cosmetic = await client.patch(
        f"/api/v1/case-statuses/{open_status['id']}",
        json={"color": "#000000", "hidden": True},
        headers=h,
    )
    assert cosmetic.status_code == 200, cosmetic.text
    assert cosmetic.json()["color"] == "#000000"
    assert cosmetic.json()["hidden"] is True


async def test_cross_org_status_rejected_on_case(
    client: AsyncClient, org_a, org_b, builtin_roles, builtin_roles_b, analyst_a_token,
    analyst_b_token,
):
    ha, hb = _h(analyst_a_token, org_a.id), _h(analyst_b_token, org_b.id)
    # A custom status owned by org-b.
    b_status = await client.post(
        "/api/v1/case-statuses/",
        json={"label": "B-only", "stage": "open"},
        headers=hb,
    )
    b_status_id = b_status.json()["id"]
    case = await _create_case(client, ha)
    # org-a cannot move its case onto org-b's status.
    r = await client.patch(
        f"/api/v1/cases/{case['id']}", json={"status_id": b_status_id}, headers=ha
    )
    assert r.status_code == 422, r.text


async def test_filter_by_stage_and_label(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    c_open = await _create_case(client, h, title="still-open")
    c_done = await _create_case(client, h, title="finished")
    rows = (await client.get("/api/v1/case-statuses/", headers=h)).json()
    resolved = next(s for s in rows if s["label"] == "Resolved")
    await client.patch(
        f"/api/v1/cases/{c_done['id']}", json={"status_id": resolved["id"]}, headers=h
    )

    # stage key: closed -> only the finished case.
    by_stage = await client.get(
        "/api/v1/cases/", params={"filter": ["stage~eq~closed"]}, headers=h
    )
    assert {c["title"] for c in by_stage.json()["items"]} == {"finished"}

    # label key: Open -> only the still-open case.
    by_label = await client.get(
        "/api/v1/cases/", params={"filter": ["status~eq~Open"]}, headers=h
    )
    assert {c["title"] for c in by_label.json()["items"]} == {"still-open"}
    assert c_open["title"] == "still-open"
