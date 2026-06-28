"""A2: User notification feed tests."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import register_consumer, _consumers
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import UserNotification
from app.services.outbox_events import notify_feed_consumer


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


async def test_read_all_marks_visible_unread(org_a, analyst_a_token, client, session):
    for _ in range(2):
        n = UserNotification(
            organisation_id=org_a.id, user_id=None,
            event_type="case.create", title="x", body="", payload={},
        )
        session.add(n)
    n_read = UserNotification(
        organisation_id=org_a.id, user_id=None,
        event_type="case.create", title="read", body="", payload={},
        read_at=datetime.now(UTC),
    )
    session.add(n_read)
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
        assert notifs[0].event_type == "case.create"
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
