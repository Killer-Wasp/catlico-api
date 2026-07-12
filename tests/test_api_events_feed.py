"""Pull-based outbound event feed: GET /events.

The feed is the polling twin of the WS /activity stream — same org-scoped
envelopes over the audit_outbox, same read:case gate. These tests cover the
cursor/paging contract and, critically, cross-org isolation.

Events are seeded by inserting Audit + AuditOutbox rows directly (permitted by the
spec): most product mutations don't stamp organisation_id on the audit payload, so
direct seeding gives deterministic, org-tagged rows to exercise the feed.
"""

from uuid import uuid4

from httpx import AsyncClient

from app.core.security import TokenPayload, create_access_token
from app.crud.organisation_member import add_member
from app.crud.role import create_role
from app.crud.user import create_user
from app.models.audit import Audit, AuditOutbox
from app.models.organisation_member import OrganisationMemberCreate
from app.models.role import Permission, RoleCreate
from app.models.user import UserCreate


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_event(
    session,
    org_id: str,
    *,
    object_type: str = "case",
    action: str = "create",
    object_id: str = "1",
    topic: str = "audit",
) -> int:
    """Insert one Audit + one AuditOutbox row for `org_id`; return the outbox id
    (the feed cursor)."""
    audit = Audit(
        request_id=str(uuid4()),
        action=action,
        object_type=object_type,
        object_id=object_id,
        actor="system",
        details={"summary": f"{object_type} {action}"},
    )
    session.add(audit)
    await session.flush()
    payload = {
        "request_id": audit.request_id,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "context_type": "case",
        "context_id": object_id,
        "actor": "system",
        "details": audit.details,
        "created_at": audit.created_at.isoformat(),
        "organisation_id": org_id,
    }
    outbox = AuditOutbox(audit_id=audit.id, topic=topic, payload=payload)
    session.add(outbox)
    await session.flush()
    return outbox.id


# --- Happy path + polling ---------------------------------------------------


async def test_happy_path_returns_envelopes_in_order_and_polls_forward(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    ids = [
        await _seed_event(session, org_a.id, object_type="case", action="create"),
        await _seed_event(session, org_a.id, object_type="task", action="update"),
        await _seed_event(session, org_a.id, object_type="observable", action="create"),
    ]
    await session.commit()

    r = await client.get(
        "/api/v1/events", params={"organisation_id": org_a.id}, headers=_auth(analyst_a_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Envelopes match build_event_envelope shape, oldest-first.
    events = body["events"]
    assert [e["event_type"] for e in events] == [
        "case.created",
        "task.updated",
        "observable.created",
    ]
    assert events[0]["object"]["type"] == "case"
    assert events[0]["event_id"] == events[0]["event_id"]  # present/serialisable
    # No internal columns leak.
    for e in events:
        assert "delivered_at" not in e
        assert "attempts" not in e

    assert body["next_cursor"] == ids[-1]
    assert body["has_more"] is False

    # Polling again from next_cursor yields nothing new; cursor echoes input.
    r2 = await client.get(
        "/api/v1/events",
        params={"organisation_id": org_a.id, "since": body["next_cursor"]},
        headers=_auth(analyst_a_token),
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["events"] == []
    assert body2["next_cursor"] == ids[-1]
    assert body2["has_more"] is False

    # A new event appears only on the next poll.
    new_id = await _seed_event(session, org_a.id, object_type="alert", action="create")
    await session.commit()
    r3 = await client.get(
        "/api/v1/events",
        params={"organisation_id": org_a.id, "since": body2["next_cursor"]},
        headers=_auth(analyst_a_token),
    )
    body3 = r3.json()
    assert [e["event_type"] for e in body3["events"]] == ["alert.created"]
    assert body3["next_cursor"] == new_id


# --- Pagination -------------------------------------------------------------


async def test_pagination_caps_page_and_advances_without_gaps(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    seeded = [await _seed_event(session, org_a.id, object_id=str(i)) for i in range(5)]
    await session.commit()

    collected: list[str] = []
    cursor = 0
    pages = 0
    while True:
        r = await client.get(
            "/api/v1/events",
            params={"organisation_id": org_a.id, "since": cursor, "limit": 2},
            headers=_auth(analyst_a_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["events"]) <= 2  # limit caps the page
        collected.extend(e["event_id"] for e in body["events"])
        pages += 1
        if not body["has_more"]:
            assert len(body["events"]) == 1  # last page has the remaining row
            break
        assert body["has_more"] is True
        assert len(body["events"]) == 2
        cursor = body["next_cursor"]
        assert pages < 10  # guard against an infinite loop

    # Every seeded event seen exactly once — no skips, no duplicates.
    assert len(collected) == len(seeded)
    assert len(set(collected)) == len(seeded)


async def test_limit_is_hard_capped_not_rejected(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_event(session, org_a.id)
    await session.commit()
    # A limit far above the cap is clamped, not a 422.
    r = await client.get(
        "/api/v1/events",
        params={"organisation_id": org_a.id, "limit": 100000},
        headers=_auth(analyst_a_token),
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["events"]) == 1


# --- Tenancy (critical) -----------------------------------------------------


async def test_member_of_a_never_sees_org_b_events(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    analyst_a,
    analyst_a_token,
):
    a_id = await _seed_event(session, org_a.id, object_type="case")
    await _seed_event(session, org_b.id, object_type="case")
    await _seed_event(session, org_b.id, object_type="task")
    await session.commit()

    r = await client.get(
        "/api/v1/events", params={"organisation_id": org_a.id}, headers=_auth(analyst_a_token)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["event_id"] for e in body["events"]] == [f"audit:{a_id}"]
    assert body["next_cursor"] == a_id


async def test_requesting_other_org_is_forbidden(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    analyst_a,
    analyst_a_token,
):
    await _seed_event(session, org_b.id)
    await session.commit()
    # analyst_a is a member of org_a only.
    r = await client.get(
        "/api/v1/events", params={"organisation_id": org_b.id}, headers=_auth(analyst_a_token)
    )
    assert r.status_code == 403, r.text


async def test_non_member_forbidden(
    client: AsyncClient, session, org_a, viewer_user, viewer_token
):
    await _seed_event(session, org_a.id)
    await session.commit()
    # viewer_user belongs to no organisation.
    r = await client.get(
        "/api/v1/events", params={"organisation_id": org_a.id}, headers=_auth(viewer_token)
    )
    assert r.status_code == 403, r.text


async def test_member_without_read_cap_forbidden(
    client: AsyncClient, session, org_a, builtin_roles, admin_user
):
    # A role that grants read:access (read:user/read:role) but NOT read:case.
    role = await create_role(
        session,
        RoleCreate(name="access-only", permissions=[Permission.read_access]),
        organisation_id=org_a.id,
        created_by=str(admin_user.id),
    )
    user = await create_user(
        session,
        UserCreate(
            first_name="No", last_name="Cap", email="nocap-a@test.com", password="password123"
        ),
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=role.id),
        created_by=str(admin_user.id),
    )
    await session.commit()
    token = create_access_token(
        TokenPayload(user_id=user.id, is_superadmin=False, organisations=[org_a.id])
    )

    await _seed_event(session, org_a.id)
    await session.commit()

    r = await client.get(
        "/api/v1/events", params={"organisation_id": org_a.id}, headers=_auth(token)
    )
    assert r.status_code == 403, r.text
    assert "read:case" in r.json()["detail"]


# --- Edge: nothing new / beyond retention -----------------------------------


async def test_since_beyond_window_returns_empty_and_echoes_cursor(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_event(session, org_a.id)
    await session.commit()
    r = await client.get(
        "/api/v1/events",
        params={"organisation_id": org_a.id, "since": 999999},
        headers=_auth(analyst_a_token),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["events"] == []
    assert body["next_cursor"] == 999999
    assert body["has_more"] is False
