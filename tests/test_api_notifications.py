"""A2: User notification feed tests."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud import notification as notif_crud
from app.crud.audit import register_consumer, _consumers
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import UserNotification, UserNotificationRead
from app.services.notification_catalog import catalog_event_types
from app.services.outbox_events import notify_feed_consumer


def _auth(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def test_feed_returns_notifications(org_a, analyst_a_token, client, session):
    n = UserNotification(
        organisation_id=org_a.id, user_id=None,
        event_type="case.create", title="Case created",
        body="A case was created", payload={"key": "val"},
    )
    session.add(n)
    await session.commit()

    resp = await client.get(
        "/api/v1/notifications/",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


async def test_mark_read_updates_notification(org_a, analyst_a_token, client, session):
    n = UserNotification(
        organisation_id=org_a.id, user_id=None,
        event_type="case.create", title="Case created", body="", payload={},
    )
    session.add(n)
    await session.commit()

    resp = await client.patch(
        f"/api/v1/notifications/{n.id}",
        json={"read_at": datetime.now(UTC).isoformat()},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["read_at"] is not None


async def test_read_all_marks_visible_unread(org_a, analyst_a, analyst_a_token, client, session):
    for _ in range(2):
        n = UserNotification(
            organisation_id=org_a.id, user_id=None,
            event_type="case.create", title="x", body="", payload={},
        )
        session.add(n)
    n_read = UserNotification(
        organisation_id=org_a.id, user_id=None,
        event_type="case.create", title="read", body="", payload={},
    )
    session.add(n_read)
    await session.flush()
    await notif_crud.set_read_state(session, n_read.id, analyst_a.id, datetime.now(UTC))
    await session.commit()

    resp = await client.post(
        "/api/v1/notifications/read-all",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["marked_read"] == 2


async def test_cross_org_user_cannot_see_notifications(org_a, org_b, analyst_a_token, client, session):
    n = UserNotification(
        organisation_id=org_b.id, user_id=None,
        event_type="case.create", title="x", body="", payload={},
    )
    session.add(n)
    await session.commit()

    resp = await client.get(
        "/api/v1/notifications/",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


async def test_outbox_consumer_creates_notification(org_a, session):
    case = Case(title="test", created_by="system", organisation_id=org_a.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org_a.id,
    )
    await session.commit()

    saved = list(_consumers)
    _consumers.clear()
    register_consumer(notify_feed_consumer)
    try:
        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered >= 1
        result = await session.execute(
            select(UserNotification).where(UserNotification.organisation_id == org_a.id)
        )
        notifs = result.scalars().all()
        assert len(notifs) >= 1
        assert notifs[0].event_type == "case.created"
    finally:
        _consumers.clear()
        _consumers.extend(saved)


async def test_notification_not_created_for_other_org(org_a, org_b, session):
    case = Case(title="test", created_by="system", organisation_id=org_a.id)
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session, action="create", obj=case, context=case, actor="user-test",
        organisation_id=org_a.id,
    )
    await session.commit()

    saved = list(_consumers)
    _consumers.clear()
    register_consumer(notify_feed_consumer)
    try:
        await audit_crud.dispatch_pending_outbox(session)
        result = await session.execute(
            select(UserNotification).where(UserNotification.organisation_id == org_a.id)
        )
        assert len(result.scalars().all()) >= 1
        result2 = await session.execute(
            select(UserNotification).where(UserNotification.organisation_id == org_b.id)
        )
        assert len(result2.scalars().all()) == 0
    finally:
        _consumers.clear()
        _consumers.extend(saved)


# --- A2: notification preferences ---


async def test_get_preferences_defaults_all_enabled(org_a, analyst_a_token, client):
    resp = await client.get(
        "/api/v1/notifications/preferences", headers=_auth(analyst_a_token, org_a)
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {i["event_type"] for i in items} == catalog_event_types()
    assert all(i["enabled"] is True for i in items)


async def test_put_preferences_persists(org_a, analyst_a_token, client):
    resp = await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": False}},
        headers=_auth(analyst_a_token, org_a),
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/api/v1/notifications/preferences", headers=_auth(analyst_a_token, org_a)
    )
    items = {i["event_type"]: i["enabled"] for i in resp.json()["items"]}
    assert items["task.updated"] is False
    assert items["case.created"] is True


async def test_disabled_event_type_hidden_from_feed(org_a, analyst_a_token, client, session):
    await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="task.updated", title="Task updated",
    )
    await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="Case created",
    )
    await session.commit()

    resp = await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": False}},
        headers=_auth(analyst_a_token, org_a),
    )
    assert resp.status_code == 200

    resp = await client.get(
        "/api/v1/notifications/", headers=_auth(analyst_a_token, org_a)
    )
    data = resp.json()
    event_types = {i["event_type"] for i in data["items"]}
    assert "task.updated" not in event_types
    assert "case.created" in event_types
    assert data["total"] == 1


async def test_read_all_skips_muted_notification(org_a, analyst_a, analyst_a_token, client, session):
    muted = await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="task.updated", title="Task updated",
    )
    await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="Case created",
    )
    await session.commit()

    await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": False}},
        headers=_auth(analyst_a_token, org_a),
    )

    resp = await client.post(
        "/api/v1/notifications/read-all", headers=_auth(analyst_a_token, org_a)
    )
    assert resp.status_code == 200
    assert resp.json()["marked_read"] == 1

    result = await session.execute(
        select(UserNotificationRead).where(
            UserNotificationRead.notification_id == muted.id,
            UserNotificationRead.user_id == analyst_a.id,
        )
    )
    assert result.scalar_one_or_none() is None


async def test_mute_is_per_user(org_a, analyst_a_token, readonly_a_token, client, session):
    await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="task.updated", title="Task updated",
    )
    await session.commit()

    await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": False}},
        headers=_auth(analyst_a_token, org_a),
    )

    resp = await client.get(
        "/api/v1/notifications/", headers=_auth(readonly_a_token, org_a)
    )
    event_types = {i["event_type"] for i in resp.json()["items"]}
    assert "task.updated" in event_types


async def test_put_preferences_unknown_event_type_422(org_a, analyst_a_token, client):
    resp = await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"not.a.real.event": False}},
        headers=_auth(analyst_a_token, org_a),
    )
    assert resp.status_code == 422


async def test_reenabling_event_makes_it_visible(org_a, analyst_a_token, client, session):
    await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="task.updated", title="Task updated",
    )
    await session.commit()

    await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": False}},
        headers=_auth(analyst_a_token, org_a),
    )
    resp = await client.get("/api/v1/notifications/", headers=_auth(analyst_a_token, org_a))
    assert resp.json()["total"] == 0

    await client.put(
        "/api/v1/notifications/preferences",
        json={"preferences": {"task.updated": True}},
        headers=_auth(analyst_a_token, org_a),
    )
    resp = await client.get("/api/v1/notifications/", headers=_auth(analyst_a_token, org_a))
    assert resp.json()["total"] == 1


async def test_read_all_is_idempotent_under_existing_receipts(
    session, client, org_a, analyst_a, analyst_a_token
):
    """read-all must not 500 when a receipt already exists for a notification
    (concurrent/repeat calls); it inserts only the still-unread ones."""
    n1 = await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="one",
    )
    n2 = await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="two",
    )
    # Pre-seed a read receipt for n1 (simulates a prior/concurrent mark).
    await notif_crud.set_read_state(session, n1.id, analyst_a.id, datetime.now(UTC))
    await session.commit()

    # First real read-all: only n2 remains unread.
    resp = await client.post(
        "/api/v1/notifications/read-all", headers=_auth(analyst_a_token, org_a)
    )
    assert resp.status_code == 200
    assert resp.json()["marked_read"] == 1

    # Second read-all: nothing left unread, must not error.
    resp = await client.post(
        "/api/v1/notifications/read-all", headers=_auth(analyst_a_token, org_a)
    )
    assert resp.status_code == 200
    assert resp.json()["marked_read"] == 0


async def test_mark_all_read_insert_survives_conflicting_receipt(
    session, org_a, analyst_a
):
    """Unit-level reproduction of the two-writer race `on_conflict_do_nothing`
    guards against: two overlapping transactions (two `read-all` tabs, or
    `read-all` racing a `PATCH /{id}`) can both snapshot a notification as
    unread via the anti-join before either writes a receipt, then collide on
    `uq_user_notification_read` when both try to insert one. We simulate that
    stale snapshot directly: capture both notifications as "unread" up front,
    let a competing writer commit a receipt for one of them, then attempt the
    insert built from the stale snapshot. Without
    `.on_conflict_do_nothing(...)` this raises `UniqueViolationError`; with it
    (Fix 1), the pre-existing receipt is skipped and only the genuinely new
    row is counted."""
    n1 = await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="one",
    )
    n2 = await notif_crud.create_notification(
        session, organisation_id=org_a.id, user_id=None,
        event_type="case.created", title="two",
    )
    await session.commit()

    # Snapshot taken while both are still unread (mirrors mark_all_read's
    # anti-join SELECT before any receipt exists).
    stale_unread_ids = [n1.id, n2.id]

    # A concurrent writer (another read-all tab, or PATCH /{id}) marks n1 read
    # and commits before the snapshot's INSERT runs.
    await notif_crud.set_read_state(session, n1.id, analyst_a.id, datetime.now(UTC))
    await session.commit()

    now = datetime.now(UTC)
    stmt = pg_insert(UserNotificationRead).values(
        [
            {
                "id": uuid.uuid4(),
                "notification_id": nid,
                "user_id": analyst_a.id,
                "read_at": now,
            }
            for nid in stale_unread_ids
        ]
    ).on_conflict_do_nothing(index_elements=["notification_id", "user_id"])
    result = await session.execute(stmt)
    await session.flush()
    await session.commit()

    # Only n2 was actually inserted; n1's pre-existing receipt was skipped
    # (not errored, and not overwritten by the stale insert).
    assert result.rowcount == 1


async def test_org_wide_read_state_is_per_user(
    session, client, org_a, analyst_a_token, readonly_a_token
):
    """User A marking an org-wide notification read must NOT mark it read for
    user B. (Regression: read_at used to live on the shared row.)"""
    notif = await notif_crud.create_notification(
        session,
        organisation_id=org_a.id,
        user_id=None,
        event_type="case.created",
        title="Case created",
    )
    await session.commit()

    resp = await client.patch(
        f"/api/v1/notifications/{notif.id}",
        json={"read_at": "2026-07-13T00:00:00Z"},
        headers=_auth(analyst_a_token, org_a),
    )
    assert resp.status_code == 200
    assert resp.json()["read_at"] is not None

    resp = await client.get(
        "/api/v1/notifications/",
        params={"unread": True},
        headers=_auth(readonly_a_token, org_a),
    )
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(notif.id) in ids, "must still be unread for the other user"
