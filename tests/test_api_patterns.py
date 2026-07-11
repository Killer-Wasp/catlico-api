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
            {"external_id": "T1566", "name": "Phishing", "tactic": "initial-access"},
            {"external_id": "T1059", "name": "Command Execution", "tactic": "execution"},
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
        f"/api/v1/{case.id}/procedures",
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

    listed = await client.get(f"/api/v1/{case.id}/procedures", headers=h)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # Replace with empty set clears them.
    cleared = await client.put(
        f"/api/v1/{case.id}/procedures", json={"procedures": []}, headers=h
    )
    assert cleared.status_code == 200
    assert cleared.json() == []
    assert (await client.get(f"/api/v1/{case.id}/procedures", headers=h)).json() == []


async def test_replace_procedures_requires_write(
    client: AsyncClient, session, org_a, builtin_roles, readonly_a, readonly_a_token
):
    """A read-only member (no write:case) cannot mutate procedures."""
    case = await _make_case(session, org_a, builtin_roles, readonly_a)
    h = _headers(readonly_a_token, org_a.id)
    r = await client.put(
        f"/api/v1/{case.id}/procedures",
        json={"procedures": [{"external_id": "T1566", "name": "Phishing"}]},
        headers=h,
    )
    assert r.status_code == 403, r.text
