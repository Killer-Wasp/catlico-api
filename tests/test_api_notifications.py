"""A2: User notification feed tests — feed endpoints, visibility, read/unread."""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud.audit import register_consumer, _consumers
from app.main import app
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import UserNotification
from app.services.outbox_events import notify_feed_consumer


async def _a_case(session, org_id: str) -> Case:
    case = Case(title="test", created_by="system", organisation_id=org_id)
    session.add(case)
    await session.flush()
    return case


async def _create_notification(session, org_id: str, *, user_id=None, read=False) -> UserNotification:
    n = UserNotification(
        organisation_id=org_id,
        user_id=user_id,
        event_type="case.create",
        title="Case created",
        body="A case was created",
        payload={"key": "val"},
    )
    if read:
        n.read_at = datetime.now(UTC)
    session.add(n)
    await session.flush()
    return n


# ---------------------------------------------------------------------------
# Feed visibility
# ---------------------------------------------------------------------------


async def test_feed_returns_own_org_notifications(org, user_client):
    """User sees notifications in their active org."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/notifications/",
            headers={"Authorization": f"Bearer {user_client['token']}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data


async def test_mark_read_updates_notification(session, org, user_client):
    """PATCH marks a notification as read."""
    n = await _create_notification(session, org.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/notifications/{n.id}",
            json={"read_at": datetime.now(UTC).isoformat()},
            headers={"Authorization": f"Bearer {user_client['token']}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["read_at"] is not None


async def test_read_all_marks_visible_unread(session, org, user_client):
    """POST read-all marks only current user's visible unread notifications."""
    await _create_notification(session, org.id)
    await _create_notification(session, org.id)
    # Already-read notification should not be affected
    await _create_notification(session, org.id, read=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/notifications/read-all",
            headers={"Authorization": f"Bearer {user_client['token']}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["marked_read"] == 2


async def test_cross_org_user_cannot_see_notifications(session, org, other_org, user_client):
    """Users in org A cannot see notifications from org B."""
    await _create_notification(session, other_org.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/notifications/",
            headers={"Authorization": f"Bearer {user_client['token']}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    # User is in `org`, not `other_org` — should see 0 items
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# Outbox consumer integration
# ---------------------------------------------------------------------------


async def test_outbox_consumer_creates_notification(session, org):
    """When the outbox is drained with the feed consumer registered, a
    notification row is created."""
    case = await _a_case(session, org.id)
    await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    # Consume the outbox
    consumer_registered = notify_feed_consumer not in _consumers
    if consumer_registered:
        register_consumer(notify_feed_consumer)
    try:
        delivered = await audit_crud.dispatch_pending_outbox(session)
        assert delivered >= 1

        # Verify notification was created
        result = await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == org.id
            )
        )
        notifs = result.scalars().all()
        assert len(notifs) >= 1
        assert notifs[0].event_type == "case.create"
    finally:
        if consumer_registered:
            _consumers.remove(notify_feed_consumer)


async def test_notification_not_created_for_other_org(session, org, other_org):
    """Notification consumer only creates rows for the event's organisation."""
    case = await _a_case(session, org.id)
    await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-test",
        organisation_id=org.id,
    )
    await session.commit()

    consumer_registered = notify_feed_consumer not in _consumers
    if consumer_registered:
        register_consumer(notify_feed_consumer)
    try:
        await audit_crud.dispatch_pending_outbox(session)

        # Org's notification exists
        result = await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == org.id
            )
        )
        assert len(result.scalars().all()) >= 1

        # Other org has none
        result2 = await session.execute(
            select(UserNotification).where(
                UserNotification.organisation_id == other_org.id
            )
        )
        assert len(result2.scalars().all()) == 0
    finally:
        if consumer_registered:
            _consumers.remove(notify_feed_consumer)
