"""Per-user assignment routing in `notify_feed_consumer`.

An assignment of a case/task creates BOTH the unchanged org-wide notification
(user_id=None) AND a targeted notification for the assignee, pushed over the WS
hub via `send_to_user`. A non-assignment event creates only the org-wide row.
"""

from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud import notification as notif_crud
from app.models.audit import AuditOutbox
from app.models.case_ import Case
from app.models.notification import UserNotification
from app.services import outbox_events
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

    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    org_wide = [n for n in notifs if n.user_id is None]
    targeted = [n for n in notifs if n.user_id == analyst_a.id]

    assert len(org_wide) == 1  # unchanged behaviour
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.assigned"
    assert targeted[0].title.startswith("You were assigned case")

    # The cross-replica push is a NOTIFY carrying a *reference* to the notification
    # row (org, target user, notification id) — the message frame itself is rebuilt
    # by the receiving EventListener (see test_ha_ws_fanout). The row's event_type
    # (asserted above) is what the frame carries.
    mock_pub.assert_awaited_once()
    _, kwargs = mock_pub.call_args
    assert kwargs["org_id"] == org_a.id
    assert kwargs["user_id"] == str(analyst_a.id)
    assert kwargs["notification_id"] == str(targeted[0].id)


async def test_create_with_bare_assignee_string_is_routed(
    session, org_a, analyst_a, monkeypatch
):
    """A bare `assignee_id` string (create-with-assignee) also routes to the user."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": str(analyst_a.id)},
    )

    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert any(n.user_id == analyst_a.id for n in notifs)
    mock_pub.assert_awaited_once()


async def test_no_assignee_change_creates_only_org_wide(
    session, org_a, monkeypatch
):
    """An event with no assignee change → only the org-wide row, no push."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"status": ["open", "closed"]},
    )

    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_pub.assert_not_called()


async def test_cleared_assignee_is_not_routed(session, org_a, monkeypatch):
    """Un-assigning ([old, None]) sets no new assignee → no targeted row/push."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": ["11111111-1111-1111-1111-111111111111", None]},
    )

    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_pub.assert_not_called()


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

    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)  # must not raise

    notifs = await _user_notifs(session, org_a.id)
    assert len(notifs) == 1
    assert notifs[0].user_id is None
    mock_pub.assert_not_called()


async def test_consumer_rerun_does_not_duplicate_notifications(
    session, org_a, analyst_a, monkeypatch
):
    """Re-running the feed consumer on the same outbox row (drain retry after a
    later consumer failed) must not create duplicate org-wide or targeted rows,
    and must not re-run the WS push."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)
    # Spy on the module logger's `exception`: the WS-push block swallows and logs
    # any error. `caplog` cannot see logs emitted inside async tests under this
    # project's pytest-asyncio setup, so we assert on the logger call directly.
    logged_errors: list[tuple] = []
    monkeypatch.setattr(
        outbox_events.logger, "exception", lambda *a, **k: logged_errors.append(a)
    )

    await notify_feed_consumer(session, row)
    await notify_feed_consumer(session, row)  # retry

    notifs = await _user_notifs(session, org_a.id)
    org_wide = [n for n in notifs if n.user_id is None]
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(org_wide) == 1
    assert len(targeted) == 1
    # The retry must skip the WS push: the `if notif is None` guard returns early.
    # Without the guard the retry falls into the push block, `model_validate(None)`
    # raises, and the swallowing `except` logs it — so this pins the guard.
    # (send_to_user alone can't: model_validate throws before it is ever reached.)
    mock_pub.assert_awaited_once()
    assert logged_errors == []


async def test_alert_assignment_creates_targeted_notification(
    session, org_a, analyst_a, monkeypatch
):
    """Alert (re)assignment routes a targeted `alert.assigned` notification —
    alerts were previously excluded from per-user routing."""
    from app.models.alert import Alert

    alert = Alert(
        type="siem",
        source="test",
        source_ref="ref-1",
        title="alert",
        description="",
        severity=2,
        tlp=2,
        pap=2,
        organisation_id=org_a.id,
        created_by="system",
    )
    session.add(alert)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=alert,
        actor="system",
        details={"assignee_id": [None, str(analyst_a.id)]},
        organisation_id=org_a.id,
    )
    await session.flush()
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    row = result.scalars().one()
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(targeted) == 1
    assert targeted[0].event_type == "alert.assigned"


async def test_assignment_event_type_is_distinct_from_updated(
    session, org_a, analyst_a, monkeypatch
):
    """The targeted row carries `<obj>.assigned`, distinct from the org-wide
    `<obj>.updated`, so muting `case.updated` does not mute assignments."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert targeted[0].event_type == "case.assigned"
    org_wide = [n for n in notifs if n.user_id is None]
    assert org_wide[0].event_type == "case.updated"  # org-wide row unchanged


async def test_create_with_assignee_notifies(session, org_a, analyst_a, monkeypatch):
    """A `create` audit carrying a bare-string assignee_id (create-with-assignee)
    produces a targeted `case.assigned` notification."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"title": "pre-assigned", "assignee_id": str(analyst_a.id)},
    )
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.assigned"


async def test_muted_event_type_creates_row_but_skips_push(
    session, org_a, analyst_a, monkeypatch
):
    """When the assignee has muted `case.assigned`, the targeted DB row is still
    created (the feed hides it on read) but NO live `{"type":"notification"}` WS
    frame is pushed — push agrees with feed-read."""
    await notif_crud.set_user_preferences(
        session,
        org_a.id,
        analyst_a.id,
        {"case.assigned": False},
        actor="admin@test.com",
    )
    await session.flush()

    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(targeted) == 1  # DB row still created
    assert targeted[0].event_type == "case.assigned"
    # Muted → no live push for this user.
    mock_pub.assert_not_called()


async def test_muting_a_different_type_does_not_suppress_assignment_push(
    session, org_a, analyst_a, monkeypatch
):
    """Muting an unrelated type (`case.updated`) must NOT suppress the
    `case.assigned` live push — only the actual muted type is skipped."""
    await notif_crud.set_user_preferences(
        session,
        org_a.id,
        analyst_a.id,
        {"case.updated": False},
        actor="admin@test.com",
    )
    await session.flush()

    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    notifs = await _user_notifs(session, org_a.id)
    targeted = [n for n in notifs if n.user_id == analyst_a.id]
    assert len(targeted) == 1
    mock_pub.assert_awaited_once()
    # The muted-type check happens before publish; the published row is the
    # case.assigned notification (its type is asserted on the row above).
    _, kwargs = mock_pub.call_args
    assert kwargs["notification_id"] == str(targeted[0].id)
    assert targeted[0].event_type == "case.assigned"


async def test_non_muting_user_still_gets_push(
    session, org_a, analyst_a, monkeypatch
):
    """A user with no mute preference for the type gets the live push as before."""
    row = await _outbox_row_for_update(
        session,
        org_id=org_a.id,
        details={"assignee_id": [None, str(analyst_a.id)]},
    )
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    mock_pub.assert_awaited_once()


async def test_case_and_task_create_stamp_assignee_into_audit(
    session, org_a, builtin_roles, analyst_a
):
    """create_case / create_task with an assignee stamp `assignee_id` into the
    audit details so the feed consumer can route a create-time assignment."""
    from app.crud import case_ as case_crud
    from app.crud import task as task_crud
    from app.models.case_ import CaseCreate
    from app.models.task import TaskCreate

    case = await case_crud.create_case(
        session,
        CaseCreate(title="c", assignee_id=analyst_a.id),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    case_row = result.scalars().one()
    assert case_row.payload["details"].get("assignee_id") == str(analyst_a.id)

    await task_crud.create_task(
        session,
        TaskCreate(title="t", assignee_id=analyst_a.id),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    task_row = result.scalars().one()
    assert task_row.payload["details"].get("assignee_id") == str(analyst_a.id)
