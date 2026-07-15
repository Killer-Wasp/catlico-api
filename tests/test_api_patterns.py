"""F1: MITRE ATT&CK pattern import + case-scoped procedures."""
import pytest
from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate


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


async def test_import_and_list_patterns(
    client: AsyncClient, org_a, admin_token, analyst_a, analyst_a_token
):
    # Import is an admin surface (write:organisation); listing is open to members.
    admin_h = _headers(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/patterns/import",
        json=[
            {"external_id": "T1566", "name": "Phishing", "tactics": ["initial-access"]},
            {"external_id": "T1059", "name": "Command Execution", "tactics": ["execution"]},
        ],
        headers=admin_h,
    )
    assert r.status_code == 200, r.text
    assert {p["external_id"] for p in r.json()} == {"T1566", "T1059"}

    listed = await client.get(
        "/api/v1/patterns", headers=_headers(analyst_a_token, org_a.id)
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert {p["external_id"] for p in body["items"]} == {"T1566", "T1059"}

    by_id = {p["external_id"]: p for p in body["items"]}
    assert by_id["T1566"]["tactics"] == ["initial-access"]


async def test_pattern_supports_multiple_tactics(
    client: AsyncClient, org_a, admin_token
):
    h = _headers(admin_token, org_a.id)
    r = await client.post(
        "/api/v1/patterns/import",
        json=[{
            "external_id": "T1078",
            "name": "Valid Accounts",
            "tactics": ["defense-evasion", "persistence", "privilege-escalation", "initial-access"],
        }],
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["tactics"] == [
        "defense-evasion", "persistence", "privilege-escalation", "initial-access",
    ]


async def test_import_upserts_by_external_id(
    client: AsyncClient, org_a, admin_token, analyst_a, analyst_a_token
):
    h = _headers(admin_token, org_a.id)
    await client.post(
        "/api/v1/patterns/import",
        json=[{"external_id": "T1566", "name": "Phishing"}],
        headers=h,
    )
    # Re-import same external_id with a new name → updates, does not duplicate.
    await client.post(
        "/api/v1/patterns/import",
        json=[{"external_id": "T1566", "name": "Phishing (updated)"}],
        headers=h,
    )
    body = (
        await client.get("/api/v1/patterns", headers=_headers(analyst_a_token, org_a.id))
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Phishing (updated)"


async def test_import_requires_write_organisation(
    client: AsyncClient, org_a, readonly_a, readonly_a_token
):
    """The global pattern catalog is admin-writable only: a member without
    write:org gets 403 instead of silently upserting shared reference data."""
    r = await client.post(
        "/api/v1/patterns/import",
        json=[{"external_id": "T1566", "name": "Phishing"}],
        headers=_headers(readonly_a_token, org_a.id),
    )
    assert r.status_code == 403, r.text


async def test_replace_and_list_procedures(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a)

    put = await client.put(
        f"/api/v1/cases/{case.id}/procedures",
        json={
            "procedures": [
                {"external_id": "T1566", "name": "Phishing", "description": "spear-phish"}
            ]
        },
        headers=h,
    )
    assert put.status_code == 200, put.text
    procs = put.json()
    assert len(procs) == 1
    assert procs[0]["description"] == "spear-phish"
    assert procs[0]["pattern"]["external_id"] == "T1566"

    listed = await client.get(f"/api/v1/cases/{case.id}/procedures", headers=h)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # Replace with empty set clears them.
    cleared = await client.put(
        f"/api/v1/cases/{case.id}/procedures", json={"procedures": []}, headers=h
    )
    assert cleared.status_code == 200
    assert cleared.json() == []
    assert (await client.get(f"/api/v1/cases/{case.id}/procedures", headers=h)).json() == []


async def test_replace_procedures_requires_write(
    client: AsyncClient, session, org_a, builtin_roles, readonly_a, readonly_a_token
):
    """A read-only member (no write:case) cannot mutate procedures."""
    case = await _make_case(session, org_a, builtin_roles, readonly_a)
    h = _headers(readonly_a_token, org_a.id)
    r = await client.put(
        f"/api/v1/cases/{case.id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=h,
    )
    assert r.status_code == 403, r.text


async def _link_ttp(client, h, case_id, external_id, name):
    r = await client.put(
        f"/api/v1/cases/{case_id}/procedures",
        json={"procedures": [{"external_id": external_id, "name": name}]},
        headers=h,
    )
    assert r.status_code == 200, r.text


async def test_case_stats_counts_distinct_cases_per_technique(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case1 = await _make_case(session, org_a, builtin_roles, analyst_a)
    case2 = await _make_case(session, org_a, builtin_roles, analyst_a)
    await _link_ttp(client, h, case1.id, "T1566", "Phishing")
    await _link_ttp(client, h, case2.id, "T1566", "Phishing")

    r = await client.get("/api/v1/patterns/case-stats", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"T1566": 2}


async def test_case_stats_is_org_scoped(
    client: AsyncClient, session, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    hb = _headers(analyst_b_token, org_b.id)
    case_a = await _make_case(session, org_a, builtin_roles, analyst_a)
    await _link_ttp(client, ha, case_a.id, "T1059", "Command Execution")

    assert (await client.get("/api/v1/patterns/case-stats", headers=ha)).json() == {
        "T1059": 1
    }
    assert (await client.get("/api/v1/patterns/case-stats", headers=hb)).json() == {}


async def test_cases_by_technique(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    await _link_ttp(client, h, case.id, "T1566", "Phishing")

    r = await client.get("/api/v1/patterns/T1566/cases", headers=h)
    assert r.status_code == 200, r.text
    cases = r.json()
    assert [c["id"] for c in cases] == [case.id]
    assert cases[0]["title"] == "c"

    missing = await client.get("/api/v1/patterns/T0000/cases", headers=h)
    assert missing.status_code == 404


async def test_case_stats_dedups_one_case_with_repeated_technique(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    """One case linked to the same technique via two procedure rows must count
    once. replace_procedures inserts one Procedure per list item without
    deduping, so a single PUT with T1566 listed twice yields 2 Procedure rows
    for 1 case — proving COUNT(DISTINCT case_id) collapses them to 1."""
    h = _headers(analyst_a_token, org_a.id)
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    r = await client.put(
        f"/api/v1/cases/{case.id}/procedures",
        json={
            "procedures": [
                {"external_id": "T1566", "name": "Phishing"},
                {"external_id": "T1566", "name": "Phishing"},
            ]
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2  # two Procedure rows for the one case

    stats = await client.get("/api/v1/patterns/case-stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert stats.json() == {"T1566": 1}


async def test_cases_by_technique_is_org_scoped(
    client: AsyncClient, session, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    hb = _headers(analyst_b_token, org_b.id)
    case_a = await _make_case(session, org_a, builtin_roles, analyst_a)
    await _link_ttp(client, ha, case_a.id, "T1566", "Phishing")

    mine = await client.get("/api/v1/patterns/T1566/cases", headers=ha)
    assert mine.status_code == 200, mine.text
    assert [c["id"] for c in mine.json()] == [case_a.id]

    theirs = await client.get("/api/v1/patterns/T1566/cases", headers=hb)
    assert theirs.status_code == 200, theirs.text
    assert theirs.json() == []


# --- Alert TTPs (§4.1a: entity-polymorphic Procedure) -----------------------


def _alert_payload(**overrides):
    base = {
        "type": "phishing",
        "source": "mail-gw",
        "source_ref": "evt-ttp",
        "title": "Suspicious email",
        "description": "user reported",
        "severity": 2,
    }
    base.update(overrides)
    return base


async def _make_alert(client, h, **overrides):
    r = await client.post("/api/v1/alerts/", json=_alert_payload(**overrides), headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_replace_and_list_alert_procedures(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    alert_id = await _make_alert(client, h)

    put = await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={
            "procedures": [
                {"external_id": "T1566", "name": "Phishing", "description": "spear-phish"}
            ]
        },
        headers=h,
    )
    assert put.status_code == 200, put.text
    procs = put.json()
    assert len(procs) == 1
    assert procs[0]["alert_id"] == alert_id
    assert procs[0]["case_id"] is None
    assert procs[0]["description"] == "spear-phish"
    assert procs[0]["pattern"]["external_id"] == "T1566"

    listed = await client.get(f"/api/v1/alerts/{alert_id}/procedures", headers=h)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # Replace with empty set clears them.
    cleared = await client.put(
        f"/api/v1/alerts/{alert_id}/procedures", json={"procedures": []}, headers=h
    )
    assert cleared.status_code == 200
    assert cleared.json() == []
    assert (
        await client.get(f"/api/v1/alerts/{alert_id}/procedures", headers=h)
    ).json() == []


async def test_replace_alert_procedures_requires_write(
    client: AsyncClient, org_a, builtin_roles,
    analyst_a_token, readonly_a, readonly_a_token,
):
    """A read-only member (no write:alert) cannot mutate an alert's TTPs."""
    h = _headers(analyst_a_token, org_a.id)
    alert_id = await _make_alert(client, h)
    ro = _headers(readonly_a_token, org_a.id)
    r = await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=ro,
    )
    assert r.status_code == 403, r.text


async def test_alert_procedures_are_org_scoped(
    client: AsyncClient, org_a, org_b, builtin_roles,
    analyst_a_token, analyst_b, analyst_b_token,
):
    """Another org can neither read nor write an alert's TTPs — the alert is
    invisible (404), same guard as GET /alerts/{id}."""
    ha = _headers(analyst_a_token, org_a.id)
    alert_id = await _make_alert(client, ha)
    await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=ha,
    )

    hb = _headers(analyst_b_token, org_b.id)
    assert (
        await client.get(f"/api/v1/alerts/{alert_id}/procedures", headers=hb)
    ).status_code == 404
    assert (
        await client.put(
            f"/api/v1/alerts/{alert_id}/procedures",
            json={"procedures": [{"external_id": "T1059", "name": "Exec"}]},
            headers=hb,
        )
    ).status_code == 404


async def test_alert_ttps_excluded_from_case_matrix(
    client: AsyncClient, org_a, builtin_roles, analyst_a_token
):
    """Alert TTPs are alert-scoped: they don't leak into the case-based ATT&CK
    matrix stats (which count DISTINCT case_id)."""
    h = _headers(analyst_a_token, org_a.id)
    alert_id = await _make_alert(client, h)
    await client.put(
        f"/api/v1/alerts/{alert_id}/procedures",
        json={"procedures": [{"external_id": "T1490", "name": "Inhibit Recovery"}]},
        headers=h,
    )
    stats = await client.get("/api/v1/patterns/case-stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert "T1490" not in stats.json()
