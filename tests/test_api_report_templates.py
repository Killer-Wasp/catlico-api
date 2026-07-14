"""G4: Report template CRUD, cross-org isolation, and case rendering."""
import pytest
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import observable as observable_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.observable import ObservableCreate
from app.models.task import TaskCreate


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


async def _add_observable(session, case, org, user, *, obs_type="ip", data="1.2.3.4"):
    return await observable_crud.create_case_observable(
        session,
        ObservableCreate(observable_type=obs_type, data=data, ioc=True),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(user.id),
    )


async def _add_task(session, case, org, user, *, title="Investigate"):
    return await task_crud.create_task(
        session,
        TaskCreate(title=title),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(user.id),
    )


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


# --- Read-back: public shape must carry the template body (§1.4a.A) ---


async def test_public_shape_includes_body(
    client: AsyncClient, org_a, analyst_a, analyst_a_token
):
    """content_md + config must round-trip on create, list, and patch so a
    management UI can load a template to edit it."""
    h = _headers(analyst_a_token, org_a.id)
    created = await _make_template(
        client, h, name="Body", content_md="# {{ title }}", config={"orientation": "portrait"}
    )
    assert created["content_md"] == "# {{ title }}"
    assert created["config"] == {"orientation": "portrait"}

    listed = await client.get("/api/v1/report-templates", headers=h)
    row = next(x for x in listed.json() if x["id"] == created["id"])
    assert row["content_md"] == "# {{ title }}"
    assert row["config"] == {"orientation": "portrait"}

    patched = await client.patch(
        f"/api/v1/report-templates/{created['id']}",
        json={"content_md": "# changed"},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["content_md"] == "# changed"
    assert patched.json()["config"] == {"orientation": "portrait"}


# --- Section expansion over case children (§1.4a.B) ---


async def test_render_observable_and_task_sections(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="Breach")
    await _add_observable(session, case, org_a, analyst_a, data="10.0.0.1")
    await _add_observable(session, case, org_a, analyst_a, obs_type="domain", data="evil.test")
    await _add_task(session, case, org_a, analyst_a, title="Triage host")

    content_md = (
        "# {{ title }}\n"
        "{{#observables}}- OBS {{type}} {{value}} ioc={{ioc}}\n{{/observables}}"
        "{{#tasks}}- TASK {{title}} [{{status}}]\n{{/tasks}}"
    )
    t = await _make_template(client, h, name="full", content_md=content_md)

    md = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    assert md.status_code == 200, md.text
    body = md.json()["content"]
    # A row per observable (2) and per task (1).
    assert "10.0.0.1" in body
    assert "evil.test" in body
    assert "Triage host" in body
    assert "Waiting" in body  # task status
    # No raw section tags survive.
    assert "{{#observables}}" not in body
    assert "{{/observables}}" not in body
    assert "{{#tasks}}" not in body


async def test_render_empty_collection_renders_nothing(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="Quiet")
    content_md = "# {{ title }}\n{{#observables}}ROWMARKER {{value}}\n{{/observables}}end"
    t = await _make_template(client, h, name="empty", content_md=content_md)

    md = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    body = md.json()["content"]
    assert "ROWMARKER" not in body  # section rendered zero times
    assert "{{#observables}}" not in body  # no raw tags left
    assert "Quiet" in body
    assert "end" in body


async def test_render_unclosed_section_does_not_corrupt(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """A section open tag with no matching close must be left intact, not eat the
    rest of the document."""
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="Safe")
    content_md = "# {{ title }}\n{{#observables}} dangling\nTRAILER"
    t = await _make_template(client, h, name="bad", content_md=content_md)
    md = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    assert md.status_code == 200, md.text
    body = md.json()["content"]
    assert "Safe" in body  # scalar still substituted
    assert "TRAILER" in body  # nothing after the dangling tag was consumed


async def test_render_timeline_summary_scalar(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="TL")
    await _add_observable(session, case, org_a, analyst_a, data="9.9.9.9")
    await _add_task(session, case, org_a, analyst_a)
    t = await _make_template(
        client, h, name="tl", content_md="Summary: {{ timeline_summary }}"
    )
    md = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    body = md.json()["content"]
    assert "{{ timeline_summary }}" not in body
    assert "1 observable" in body
    assert "1 task" in body


# --- Print-friendly HTML + markdown JSON shape (§1.4a.C) ---


async def test_html_has_print_css_wrapper(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="Printable")
    t = await _make_template(client, h, name="p", content_md="# {{ title }}")
    html = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "html"},
        headers=h,
    )
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    lowered = html.text.lower()
    assert "<style" in lowered
    assert "@page" in lowered  # print margins
    assert "Printable" in html.text


async def test_markdown_json_shape_unchanged(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a, title="Json")
    t = await _make_template(client, h, name="j", content_md="# {{ title }}")
    md = await client.get(
        f"/api/v1/report-templates/{t['id']}/render",
        params={"case_id": case.id, "fmt": "markdown"},
        headers=h,
    )
    payload = md.json()
    assert set(payload.keys()) == {"format", "content"}
    assert payload["format"] == "markdown"
    assert payload["content"].startswith("# Json")
