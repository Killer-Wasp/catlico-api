"""Extract @-mention user ids from stored markdown.

The web editor (@tiptap/markdown + @tiptap/extension-mention) serialises a
mention node into markdown as the inline token::

    [@ id="<user-uuid>" label="<display name>"]

and parses it back losslessly, so the id in stored case descriptions and
comment messages is authoritative. This module is the single place that knows
that wire format.
"""

from __future__ import annotations

import re

_MENTION_TOKEN = re.compile(r"\[@\s+([^\]]*)\]")
_ID_ATTR = re.compile(
    r'id="([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"'
)


def extract_mention_ids(text: str | None) -> set[str]:
    """All user ids @-mentioned in `text`, lowercased. Tolerant of extra
    attributes inside the token; ignores malformed tokens (missing/invalid id).

    The token match stops at the first ``]``, so a ``label`` value containing a
    literal ``]`` truncates the token — but the id is still extracted because
    tiptap always emits ``id`` before ``label``. Relies on that ordering."""
    if not text:
        return set()
    ids: set[str] = set()
    for token in _MENTION_TOKEN.finditer(text):
        match = _ID_ATTR.search(token.group(1))
        if match:
            ids.add(match.group(1).lower())
    return ids
