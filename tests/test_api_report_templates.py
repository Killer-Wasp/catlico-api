"""G4: Report template CRUD, cross-org isolation, and case rendering."""
import pytest
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user, title="Incident 1", assignee_id=None):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title, assignee_id=assignee_id),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


async def _make_template(client, headers, **body):
    body.setdefault("name", "t")
    r = await client.post("/api/v1/report-templates", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_template_crud(client: AsyncClient, org_a, analyst_a, analyst_a_token):
    h = _headers(analyst_a_token, org_a.id)
    t = await _make_template(client, h, name="Exec Summary", content_md="# {{ title }}")
    tid = t["id"]

    listed = await client.get("/api/v1/report-templates", headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == tid for x in listed.json())

    upd = await client.patch(
        f"/api/v1/report-templates/{tid}", json={"name": "Renamed"}, headers=h
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "Renamed"

    d = await client.delete(f"/api/v1/report-templates/{tid}", headers=h)
    assert d.status_code == 204
    assert not any(
        x["id"] == tid
        for x in (await client.get("/api/v1/report-templates", headers=h)).json()
    )


async def test_readonly_cannot_write(
    client: AsyncClient, org_a, readonly_a, readonly_a_token
):
    """Read-only members lack write:organisation → 403 on create."""
    h = _headers(readonly_a_token, org_a.id)
    r = await client.post(
        "/api/v1/report-templates", json={"name": "nope"}, headers=h
    )
    assert r.status_code == 403, r.text


async def test_cross_org_template_isolated(
    client: AsyncClient,
    org_a,
    analyst_a,
    analyst_a_token,
    org_b,
    analyst_b,
    analyst_b_token,
):
    """A template in org-a must be invisible to org-b (404, not another org's data)."""
    a_h = _headers(analyst_a_token, org_a.id)
    t = await _make_template(client, a_h, name="secret")
    tid = t["id"]

    b_h = _headers(analyst_b_token, org_b.id)
    # Not in org-b's list.
    assert (await client.get("/api/v1/report-templates", headers=b_h)).json() == []
    # Direct fetch/patch/delete by id are 404 across the tenant boundary.
    assert (
        await client.patch(
            f"/api/v1/report-templates/{tid}", json={"name": "hijack"}, headers=b_h
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/v1/report-templates/{tid}", headers=b_h)
    ).status_code == 404


async def test_render_html_and_markdown(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    # Assign the case so the {{ assignee }} placeholder is exercised (regression:
    # the service used to read a non-existent Case.assignee attribute → 500).
    case = await _make_case(
        session, org_a, builtin_roles, analyst_a, title="Breach", assignee_id=analyst_a.id
    )
    t = await _make_template(
        client,
        h,
        name="r",
        content_md="# {{ title }}\n- status {{ status }}\n- owner {{ assignee }}",
    )
    tid = t["id"]

    md = await client.get(
        f"/api/v1/report-templates/{tid}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    assert md.status_code == 200, md.text
    assert "Breach" in md.json()["content"]
    assert str(analyst_a.id) in md.json()["content"]

    html = await client.get(
        f"/api/v1/report-templates/{tid}/render",
        params={"case_id": case.id, "fmt": "html"},
        headers=h,
    )
    assert html.status_code == 200
    assert "Breach" in html.text
    assert "<" in html.text  # rendered to HTML, not raw markdown


async def test_render_unknown_case_404(
    client: AsyncClient, org_a, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    t = await _make_template(client, h, name="r", content_md="x")
    r = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": 999999},
        headers=h,
    )
    assert r.status_code == 404
