"""Tests for Case Templates and Tags."""
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user, title="c"):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


# --- Tags ---

async def test_set_and_list_case_tags(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.put(
        f"/api/v1/cases/{case.id}/tags",
        json={"tags": ["phishing", "tlp:amber", "kill-chain:phase=exploitation"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"phishing", "tlp:amber", "kill-chain:phase=exploitation"}

    # Replace-semantics: a second PUT sets the exact list
    r2 = await client.put(
        f"/api/v1/cases/{case.id}/tags", json={"tags": ["phishing"]}, headers=h
    )
    assert set(r2.json()) == {"phishing"}

    lst = await client.get(f"/api/v1/cases/{case.id}/tags", headers=h)
    assert lst.json() == ["phishing"]


async def test_tags_are_global_vocabulary(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """The same free tag reused across cases resolves to one tag row."""
    from sqlmodel import select

    from app.models.tag import Tag

    c1 = await _make_case(session, org_a, builtin_roles, analyst_a)
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    await client.put(f"/api/v1/cases/{c1.id}/tags", json={"tags": ["malware"]}, headers=h)
    await client.put(f"/api/v1/cases/{c2.id}/tags", json={"tags": ["malware"]}, headers=h)
    tags = (await session.execute(select(Tag).where(Tag.predicate == "malware"))).scalars().all()
    assert len(tags) == 1


# --- Templates ---

async def test_template_crud(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/case-templates/",
        json={
            "name": "phishing-playbook",
            "title_prefix": "[Phishing] ",
            "severity": 3,
            "tasks": [
                {"title": "Triage", "order": 0},
                {"title": "Contain", "order": 1, "group": "response"},
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    tpl_id = r.json()["id"]
    assert len(r.json()["tasks"]) == 2

    # Tag the template
    await client.put(
        f"/api/v1/case-templates/{tpl_id}/tags", json={"tags": ["phishing"]}, headers=h
    )

    got = await client.get(f"/api/v1/case-templates/{tpl_id}", headers=h)
    assert got.json()["tags"] == ["phishing"]
    assert got.json()["severity"] == 3

    lst = await client.get("/api/v1/case-templates/", headers=h)
    assert lst.json()["total"] == 1


async def test_create_case_from_template(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    tpl = await client.post(
        "/api/v1/case-templates/",
        json={
            "name": "pb",
            "title_prefix": "[Phishing] ",
            "severity": 4,
            "tlp": 3,
            "tasks": [{"title": "Triage", "order": 0}, {"title": "Contain", "order": 1}],
        },
        headers=h,
    )
    tpl_id = tpl.json()["id"]
    await client.put(
        f"/api/v1/case-templates/{tpl_id}/tags", json={"tags": ["phishing"]}, headers=h
    )

    # Create a case from the template; do not set severity -> template's 4 applies.
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "user reported email", "case_template_id": tpl_id},
        headers=h,
    )
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["title"] == "[Phishing] user reported email"
    assert case["severity"] == 4
    assert case["tlp"] == 3

    case_id = case["id"]
    # Tasks scaffolded
    tasks = await client.get(f"/api/v1/cases/{case_id}/tasks", headers=h)
    assert tasks.json()["total"] == 2
    assert {t["title"] for t in tasks.json()["items"]} == {"Triage", "Contain"}
    # Template tags copied to the case
    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert tags.json() == ["phishing"]


async def test_explicit_field_overrides_template(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    tpl = await client.post(
        "/api/v1/case-templates/",
        json={"name": "pb2", "severity": 4},
        headers=h,
    )
    tpl_id = tpl.json()["id"]
    # Explicit severity=1 must win over the template's 4
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "x", "severity": 1, "case_template_id": tpl_id},
        headers=h,
    )
    assert r.json()["severity"] == 1


async def test_unknown_template_rejected(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/cases/", json={"title": "x", "case_template_id": 99999}, headers=h
    )
    assert r.status_code == 422


async def test_promote_with_template_scaffolds_and_tags(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    tpl = await client.post(
        "/api/v1/case-templates/",
        json={"name": "pb3", "title_prefix": "[IR] ", "tasks": [{"title": "Investigate"}]},
        headers=h,
    )
    tpl_id = tpl.json()["id"]

    alert = await client.post(
        "/api/v1/alerts/",
        json={"type": "ids", "source": "suricata", "source_ref": "r1", "title": "hit"},
        headers=h,
    )
    alert_id = alert.json()["id"]
    await client.put(f"/api/v1/alerts/{alert_id}/tags", json={"tags": ["ids"]}, headers=h)

    promo = await client.post(
        f"/api/v1/alerts/{alert_id}/promote",
        json={"case_template_id": tpl_id},
        headers=h,
    )
    assert promo.status_code == 201, promo.text
    case_id = promo.json()["id"]
    assert promo.json()["title"] == "[IR] hit"
    tasks = await client.get(f"/api/v1/cases/{case_id}/tasks", headers=h)
    assert {t["title"] for t in tasks.json()["items"]} == {"Investigate"}
    # Alert's own tag carried onto the case
    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert "ids" in tags.json()


async def test_template_org_scoped(
    client: AsyncClient, org_a, org_b, builtin_roles, analyst_a, analyst_a_token, analyst_b_token
):
    ha = _headers(analyst_a_token, org_a.id)
    tpl = await client.post("/api/v1/case-templates/", json={"name": "secret"}, headers=ha)
    tpl_id = tpl.json()["id"]
    hb = _headers(analyst_b_token, org_b.id)
    assert (await client.get(f"/api/v1/case-templates/{tpl_id}", headers=hb)).status_code == 404


# --- Template import / export ---

async def test_export_then_import_roundtrip(
    client: AsyncClient, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    tpl = await client.post(
        "/api/v1/case-templates/",
        json={
            "name": "shareable",
            "title_prefix": "[PB] ",
            "severity": 3,
            "tasks": [{"title": "Step1", "order": 0}, {"title": "Step2", "order": 1}],
        },
        headers=ha,
    )
    tpl_id = tpl.json()["id"]
    await client.put(
        f"/api/v1/case-templates/{tpl_id}/tags", json={"tags": ["phishing"]}, headers=ha
    )

    exp = await client.get(f"/api/v1/case-templates/{tpl_id}/export", headers=ha)
    assert exp.status_code == 200, exp.text
    doc = exp.json()
    assert doc["kind"] == "catlico.caseTemplate"
    assert doc["name"] == "shareable"
    assert len(doc["tasks"]) == 2
    assert doc["tags"] == ["phishing"]
    # Portable: no instance-specific fields leak
    assert "id" not in doc and "organisation_id" not in doc

    # Import into a different org (org-b)
    hb = _headers(analyst_b_token, org_b.id)
    imp = await client.post("/api/v1/case-templates/import", json=doc, headers=hb)
    assert imp.status_code == 201, imp.text
    assert imp.json()["name"] == "shareable"
    assert {t["title"] for t in imp.json()["tasks"]} == {"Step1", "Step2"}
    assert imp.json()["tags"] == ["phishing"]
    assert imp.json()["organisation_id"] == org_b.id


async def test_import_name_collision_conflicts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await client.post("/api/v1/case-templates/", json={"name": "dup"}, headers=h)
    imp = await client.post(
        "/api/v1/case-templates/import",
        json={"kind": "catlico.caseTemplate", "version": 1, "name": "dup"},
        headers=h,
    )
    assert imp.status_code == 409


async def test_import_rejects_unknown_kind(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    imp = await client.post(
        "/api/v1/case-templates/import",
        json={"kind": "something.else", "name": "x"},
        headers=h,
    )
    assert imp.status_code == 422
