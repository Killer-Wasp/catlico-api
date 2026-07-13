"""Mention-token extraction from stored markdown.

The web editor (@tiptap/markdown) serialises a mention node as the inline token
`[@ id="<uuid>" label="<display name>"]` (verified empirically 2026-07-13
against the packages in catlico-web). These tests pin that contract.
"""

from sqlmodel import select

from app.crud import comment as comment_crud
from app.crud import case_ as case_crud
from app.models.audit import AuditOutbox
from app.models.case_ import Case, CaseUpdate
from app.models.comment import CommentCreate, CommentEntityType, CommentUpdate
from app.services.mentions import extract_mention_ids

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
