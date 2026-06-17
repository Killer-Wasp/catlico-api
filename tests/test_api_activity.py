"""Activity feed (audit trail) endpoint + cross-entity audit coverage + tenancy."""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit import Audit


def _auth(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _create_case(client, token, org_id, title="incident") -> int:
    r = await client.post(
        "/api/v1/cases/", json={"title": title, "severity": 3}, headers=_auth(token, org_id)
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_case_activity_collects_case_and_child_events(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token, observable_types
):
    headers = _auth(analyst_a_token, org_a.id)
    case_id = await _create_case(client, analyst_a_token, org_a.id)

    # update the case
    r = await client.patch(
        f"/api/v1/cases/{case_id}", json={"severity": 4}, headers=headers
    )
    assert r.status_code == 200, r.text

    # task
    r = await client.post(
        f"/api/v1/cases/{case_id}/tasks", json={"title": "triage"}, headers=headers
    )
    assert r.status_code == 201, r.text

    # observable
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    # comment
    r = await client.post(
        f"/api/v1/cases/{case_id}/comments", json={"message": "looking into it"}, headers=headers
    )
    assert r.status_code == 201, r.text

    # activity feed
    r = await client.get(f"/api/v1/cases/{case_id}/activity", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()["items"]

    seen = {(i["object_type"], i["action"]) for i in items}
    assert ("case", "create") in seen
    assert ("case", "update") in seen
    assert ("task", "create") in seen
    assert ("observable", "create") in seen
    assert ("comment", "create") in seen

    # child events are scoped to the case via context, which is how they're gathered
    for i in items:
        if i["object_type"] != "case":
            assert i["context_type"] == "case"
            assert i["context_id"] == str(case_id)

    # actor is the acting user's id, recorded as a string (not a FK)
    case_create = next(i for i in items if i["object_type"] == "case" and i["action"] == "create")
    assert case_create["actor"] == str(analyst_a.id)


async def test_case_update_records_field_diff(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    headers = _auth(analyst_a_token, org_a.id)
    case_id = await _create_case(client, analyst_a_token, org_a.id)

    r = await client.patch(
        f"/api/v1/cases/{case_id}", json={"severity": 4}, headers=headers
    )
    assert r.status_code == 200

    r = await client.get(f"/api/v1/cases/{case_id}/activity", headers=headers)
    update = next(
        i for i in r.json()["items"] if i["object_type"] == "case" and i["action"] == "update"
    )
    # details capture [old, new] for changed fields
    assert update["details"]["severity"] == [3, 4]


async def test_activity_hidden_from_org_without_share(
    client: AsyncClient,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_a_token,
    analyst_b,
    analyst_b_token,
):
    # org-a owns the case
    case_id = await _create_case(client, analyst_a_token, org_a.id)

    # org-b, with no share, cannot see the case — nor its activity
    r = await client.get(
        f"/api/v1/cases/{case_id}/activity", headers=_auth(analyst_b_token, org_b.id)
    )
    assert r.status_code == 404


async def test_alert_promotion_appears_in_case_activity(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    headers = _auth(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/alerts/",
        json={"type": "siem", "source": "splunk", "source_ref": "evt-9", "title": "hit"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    alert_id = r.json()["id"]

    r = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=headers)
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]

    r = await client.get(f"/api/v1/cases/{case_id}/activity", headers=headers)
    items = r.json()["items"]
    promote = next(
        i for i in items if i["object_type"] == "alert" and i["action"] == "update"
    )
    assert promote["context_type"] == "case"
    assert promote["context_id"] == str(case_id)
    assert promote["details"]["promoted_to_case"] == case_id


async def test_admin_user_mutations_are_audited(
    client: AsyncClient, session, admin_user, admin_token
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/users/",
        json={"email": "new@test.com", "password": "password123"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    rows = (
        (
            await session.execute(
                select(Audit).where(Audit.object_type == "user", Audit.object_id == new_id)
            )
        )
        .scalars()
        .all()
    )
    assert any(a.action == "create" for a in rows)
    created = next(a for a in rows if a.action == "create")
    assert created.actor == str(admin_user.id)
    # the password must never be captured in the audit details
    assert "password123" not in str(created.details)


async def test_response_carries_request_id_header(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    r = await client.get(
        "/api/v1/cases/", headers=_auth(analyst_a_token, org_a.id)
    )
    assert r.headers.get("x-request-id")
