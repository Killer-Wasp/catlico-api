"""Per-user assignment routing in `notify_feed_consumer`.

An assignment of a case/task creates BOTH the unchanged org-wide notification
(user_id=None) AND a targeted notification for the assignee, pushed over the WS
hub via `send_to_user`. A non-assignment event creates only the org-wide row.
"""

from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import UserNotification
from app.services.outbox_events import notify_feed_consumer


async def _outbox_row_for_update(session, *, org_id: str, details: dict) -> AuditOutbox:
    """Create a case + a `case update` audit → outbox row with the given details."""
    case = Case(title="assign me", created_by="system")
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="admin@test.com",
        details=details,
        organisation_id=org_id,
    )
    await session.flush()
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    return result.scalars().one()


async def _user_notifs(session, org_id: str):
    result = await session.execute(
        select(UserNotification).where(UserNotification.organisation_id == org_id)
    )
    return result.scalars().all()


async def test_assignment_creates_targeted_notification_and_pushes(
    session, org_a, analyst_a, monkeypatch
):
    """A case update that sets an assignee → org-wide row (unchanged) + targeted
    row for the assignee + a `send_to_user` push with the right contract."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )

    mock_hub = AsyncMock()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: mock_hub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    org_wide = [n for n in notifs if n.user_id is None]
    targeted = [n for n in notifs if n.user_id == analyst_a.id]

    assert len(org_wide) == 1  # unchanged behaviour
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.updated"
    assert targeted[0].title.startswith("You were assigned case")

    mock_hub.send_to_user.assert_awaited_once()
    args, _ = mock_hub.send_to_user.call_args
    assert args[0] == org_a.id
    assert args[1] == str(analyst_a.id)
    assert args[2]["type"] == "notification"
    assert args[2]["notification"]["id"] == str(targeted[0].id)
    assert args[2]["notification"]["event_type"] == "case.updated"


async def test_create_with_bare_assignee_string_is_routed(
    session, org_a, analyst_a, monkeypatch
):
    """A bare `assignee_id` string (create-with-assignee) also routes to the user."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": str(analyst_a.id)},
    )

    mock_hub = AsyncMock()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: mock_hub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert any(n.user_id == analyst_a.id for n in notifs)
    mock_hub.send_to_user.assert_awaited_once()


async def test_no_assignee_change_creates_only_org_wide(
    session, org_a, monkeypatch
):
    """An event with no assignee change → only the org-wide row, no push."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"status": ["open", "closed"]},
    )

    mock_hub = AsyncMock()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: mock_hub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_hub.send_to_user.assert_not_called()


async def test_cleared_assignee_is_not_routed(session, org_a, monkeypatch):
    """Un-assigning ([old, None]) sets no new assignee → no targeted row/push."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": ["11111111-1111-1111-1111-111111111111", None]},
    )

    mock_hub = AsyncMock()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: mock_hub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_hub.send_to_user.assert_not_called()


async def test_org_less_event_creates_no_notification(session, org_a):
    """An audit event without organisation_id (e.g. a global user mutation) must
    be skipped by the feed consumer — not inserted with org_id='' (FK violation)."""
    from app.models.user import User

    user = User(
        email="orgless@test.com",
        first_name="Org",
        last_name="Less",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=user,
        actor="system",
        details={"full_name": ["Old", "New"]},
        # organisation_id deliberately omitted — global event
    )
    await session.flush()
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    row = result.scalars().one()
    assert "organisation_id" not in row.payload

    await notify_feed_consumer(session, row)  # must not raise IntegrityError
    await session.flush()

    result = await session.execute(select(UserNotification))
    assert result.scalars().all() == []


async def test_malformed_assignee_uuid_is_skipped(session, org_a, monkeypatch):
    """A non-uuid assignee value must not 500 the drain; org-wide row still lands."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, "not-a-uuid"]},
    )

    mock_hub = AsyncMock()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: mock_hub)

    await notify_feed_consumer(session, row)  # must not raise

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_hub.send_to_user.assert_not_called()


async def test_consumer_rerun_does_not_duplicate_notifications(
    session, org_a, analyst_a, monkeypatch
):
    """Re-running the feed consumer on the same outbox row (drain retry after a
    later consumer failed) must not create duplicate org-wide or targeted rows."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    monkeypatch.setattr(
        "app.services.websocket_hub.get_hub", lambda: AsyncMock()
    )

    await notify_feed_consumer(session, row)
    await notify_feed_consumer(session, row)  # retry

    notifs = await _user_notifs(session, org_a.id)
    org_wide = [n for n in notifs if n.user_id is None]
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(org_wide) == 1
    assert len(targeted) == 1
