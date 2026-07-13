"""Mention-token extraction from stored markdown.

The web editor (@tiptap/markdown) serialises a mention node as the inline token
`[@ id="<uuid>" label="<display name>"]` (verified empirically 2026-07-13
against the packages in catlico-web). These tests pin that contract.
"""

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
