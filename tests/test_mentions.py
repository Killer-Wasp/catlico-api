"""Mention-token extraction from stored markdown.

The web editor (@tiptap/markdown) serialises a mention node as the inline token
`[@ id="<uuid>" label="<display name>"]` (verified empirically 2026-07-13
against the packages in catlico-web). These tests pin that contract.
"""

from unittest.mock import AsyncMock

from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud import comment as comment_crud
from app.crud import case_ as case_crud
from app.models.audit import AuditOutbox
from app.models.case_ import Case, CaseUpdate
from app.models.comment import Comment, CommentCreate, CommentEntityType, CommentUpdate
from app.models.notification import UserNotification
from app.services.mentions import extract_mention_ids
from app.services.outbox_events import notify_feed_consumer

UID1 = "4b6e0f0a-1111-2222-3333-444455556666"
UID2 = "9f8e7d6c-aaaa-bbbb-cccc-ddddeeeeffff"


def test_extracts_single_mention():
    text = f'Please review, [@ id="{UID1}" label="Jane Doe"] — thanks!'
    assert extract_mention_ids(text) == {UID1}


def test_extracts_multiple_and_dedupes():
    text = (
        f'[@ id="{UID1}" label="Jane"] and [@ id="{UID2}" label="Bob"]'
        f' and again [@ id="{UID1}" label="Jane"]'
    )
    assert extract_mention_ids(text) == {UID1, UID2}


def test_ignores_plain_at_signs_and_emails():
    assert extract_mention_ids("email me at jane@example.com @jane [@]") == set()


def test_ignores_token_without_id_attr():
    assert extract_mention_ids('[@ label="Jane Doe"]') == set()


def test_handles_none_and_empty():
    assert extract_mention_ids(None) == set()
    assert extract_mention_ids("") == set()


def test_uppercase_uuid_normalised_to_lowercase():
    text = f'[@ id="{UID1.upper()}" label="J"]'
    assert extract_mention_ids(text) == {UID1}


def test_label_containing_bracket_still_extracts_id():
    """A `]` inside the label truncates the token, but because tiptap emits `id`
    before `label` the id is still recovered. Pins that ordering dependency."""
    text = f'[@ id="{UID1}" label="Ops [oncall]"] please look'
    assert extract_mention_ids(text) == {UID1}


def _mention(uid: str, label: str = "Someone") -> str:
    return f'[@ id="{uid}" label="{label}"]'


async def _latest_outbox(session) -> AuditOutbox:
    result = await session.execute(
        select(AuditOutbox).order_by(AuditOutbox.id.desc()).limit(1)
    )
    return result.scalars().one()


async def test_comment_create_stamps_mentions(session, org_a, analyst_a):
    author = "00000000-0000-0000-0000-00000000aaaa"
    await comment_crud.create_comment(
        session,
        CommentCreate(message=f"ping {_mention(str(analyst_a.id))} and {_mention(author)}"),
        entity_type=CommentEntityType.case,
        entity_id="1",
        organisation_id=org_a.id,
        created_by=author,  # self-mention must be excluded
    )
    row = await _latest_outbox(session)
    assert row.payload["details"]["mentioned_user_ids"] == [str(analyst_a.id)]


async def test_comment_create_without_mentions_stamps_nothing(session, org_a):
    await comment_crud.create_comment(
        session,
        CommentCreate(message="no mentions here"),
        entity_type=CommentEntityType.case,
        entity_id="1",
        organisation_id=org_a.id,
        created_by="00000000-0000-0000-0000-00000000aaaa",
    )
    row = await _latest_outbox(session)
    details = row.payload.get("details") or {}
    assert "mentioned_user_ids" not in details


async def test_comment_update_stamps_only_new_mentions(session, org_a, analyst_a):
    author = "00000000-0000-0000-0000-00000000aaaa"
    existing = "9f8e7d6c-aaaa-bbbb-cccc-ddddeeeeffff"
    comment = await comment_crud.create_comment(
        session,
        CommentCreate(message=f"hi {_mention(existing)}"),
        entity_type=CommentEntityType.case,
        entity_id="1",
        organisation_id=org_a.id,
        created_by=author,
    )
    await comment_crud.update_comment(
        session,
        comment,
        CommentUpdate(
            message=f"hi {_mention(existing)} and now {_mention(str(analyst_a.id))}"
        ),
        updated_by=author,
    )
    row = await _latest_outbox(session)
    assert row.payload["details"]["mentioned_user_ids"] == [str(analyst_a.id)]


async def test_case_description_update_stamps_new_mentions(session, org_a, analyst_a):
    author = "00000000-0000-0000-0000-00000000aaaa"
    case = Case(title="c", description="plain", created_by=author)
    session.add(case)
    await session.flush()
    await case_crud.update_case(
        session,
        case,
        CaseUpdate(description=f"now with {_mention(str(analyst_a.id))}"),
        updated_by=author,
        organisation_id=org_a.id,
    )
    row = await _latest_outbox(session)
    assert row.payload["details"]["mentioned_user_ids"] == [str(analyst_a.id)]


async def test_comment_mention_creates_targeted_notification(
    session, org_a, analyst_a, monkeypatch
):
    comment = Comment(
        entity_type=CommentEntityType.case,
        entity_id="1",
        message="hello",
        organisation_id=org_a.id,
        created_by="00000000-0000-0000-0000-00000000aaaa",
    )
    session.add(comment)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="create",
        obj=comment,
        context_type="case",
        context_id="1",
        actor="00000000-0000-0000-0000-00000000aaaa",
        details={"mentioned_user_ids": [str(analyst_a.id)]},
        organisation_id=org_a.id,
    )
    await session.flush()
    row = await _latest_outbox(session)
    mock_pub = AsyncMock()
    monkeypatch.setattr("app.services.event_bus.publish_user", mock_pub)

    await notify_feed_consumer(session, row)

    result = await session.execute(
        select(UserNotification).where(UserNotification.user_id == analyst_a.id)
    )
    targeted = result.scalars().all()
    assert len(targeted) == 1
    assert targeted[0].event_type == "comment.mentioned"
    assert "mentioned" in targeted[0].title
    mock_pub.assert_awaited_once()


async def test_case_description_mention_notifies(session, org_a, analyst_a, monkeypatch):
    case = Case(title="c", created_by="system")
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="system",
        details={
            "description": ["old", "new"],
            "mentioned_user_ids": [str(analyst_a.id)],
        },
        organisation_id=org_a.id,
    )
    await session.flush()
    row = await _latest_outbox(session)
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    result = await session.execute(
        select(UserNotification).where(UserNotification.user_id == analyst_a.id)
    )
    targeted = result.scalars().all()
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.mentioned"


async def test_assign_and_mention_same_user_yields_one_row_assignment_wins(
    session, org_a, analyst_a, monkeypatch
):
    """When one event both assigns AND mentions the same user, the (outbox_id,
    user_id) unique key keeps exactly one targeted row — the assignment (created
    first) wins, the mention dedups away. Pins this intentional trade-off."""
    case = Case(title="c", created_by="system")
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="system",
        details={
            "assignee_id": [None, str(analyst_a.id)],
            "mentioned_user_ids": [str(analyst_a.id)],
        },
        organisation_id=org_a.id,
    )
    await session.flush()
    row = await _latest_outbox(session)
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    result = await session.execute(
        select(UserNotification).where(UserNotification.user_id == analyst_a.id)
    )
    targeted = result.scalars().all()
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.assigned"  # assignment ran first


async def test_mention_of_nonexistent_user_does_not_poison_fanout(
    session, org_a, monkeypatch
):
    """A mention of a syntactically-valid but NONEXISTENT user id must be skipped
    (its FK violation caught in an isolated savepoint), not abort the whole
    event's fan-out — otherwise the outbox row poisons and retries forever.
    Mentions come from user-controlled markdown and are NOT validated at write
    time, so a bad/typo'd/since-deleted id is reachable."""
    ghost = "deadbeef-dead-dead-dead-deaddeaddead"
    author = "00000000-0000-0000-0000-00000000aaaa"
    comment = Comment(
        entity_type=CommentEntityType.case,
        entity_id="1",
        message="hi",
        organisation_id=org_a.id,
        created_by=author,
    )
    session.add(comment)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="create",
        obj=comment,
        context_type="case",
        context_id="1",
        actor=author,
        details={"mentioned_user_ids": [ghost]},
        organisation_id=org_a.id,
    )
    await session.flush()
    row = await _latest_outbox(session)
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)  # must NOT raise an FK violation

    result = await session.execute(
        select(UserNotification).where(UserNotification.organisation_id == org_a.id)
    )
    notifs = result.scalars().all()
    # Org-wide row landed; the ghost mention was skipped (no targeted row).
    assert len(notifs) == 1
    assert notifs[0].user_id is None


async def test_bad_mention_does_not_lose_valid_assignment_same_event(
    session, org_a, analyst_a, monkeypatch
):
    """One event assigning a REAL user and mentioning a NONEXISTENT one: the
    valid assignment notification must still land; only the bad mention is
    skipped. (The exact cross-cutting scenario the final review flagged.)"""
    ghost = "deadbeef-dead-dead-dead-deaddeaddead"
    case = Case(title="c", created_by="system")
    session.add(case)
    await session.flush()
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor="system",
        details={
            "assignee_id": [None, str(analyst_a.id)],
            "mentioned_user_ids": [ghost],
        },
        organisation_id=org_a.id,
    )
    await session.flush()
    row = await _latest_outbox(session)
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)  # must NOT raise

    result = await session.execute(
        select(UserNotification).where(UserNotification.user_id == analyst_a.id)
    )
    targeted = result.scalars().all()
    assert len(targeted) == 1
    assert targeted[0].event_type == "case.assigned"


async def test_comment_mention_end_to_end(session, org_a, analyst_a, monkeypatch):
    """Full path: real mention text through create_comment stamps the id, then the
    feed consumer routes a targeted `comment.mentioned` notification for it."""
    author = "00000000-0000-0000-0000-00000000aaaa"
    await comment_crud.create_comment(
        session,
        CommentCreate(message=f"hey {_mention(str(analyst_a.id))} take a look"),
        entity_type=CommentEntityType.case,
        entity_id="1",
        organisation_id=org_a.id,
        created_by=author,
    )
    row = await _latest_outbox(session)
    monkeypatch.setattr("app.services.websocket_hub.get_hub", lambda: AsyncMock())

    await notify_feed_consumer(session, row)

    result = await session.execute(
        select(UserNotification).where(UserNotification.user_id == analyst_a.id)
    )
    targeted = result.scalars().all()
    assert len(targeted) == 1
    assert targeted[0].event_type == "comment.mentioned"
