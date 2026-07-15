"""Audit backbone coverage + request-id header.

The user-facing case *activity timeline* endpoint (`GET /cases/{id}/activity`) was
removed in the OOS slim-down, but the audit backbone (record_audit + outbox) stays
and still writes rows — these tests assert that directly against the `audit` table.
"""

from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit import Audit


def _auth(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def test_admin_user_mutations_are_audited(
    client: AsyncClient, session, admin_user, admin_token
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/users/",
        json={
            "email": "new@test.com",
            "password": "password123-long",
            "first_name": "New",
            "last_name": "User",
        },
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
