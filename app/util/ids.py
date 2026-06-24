"""Human-readable, case-scoped identifiers for tasks, worklogs and attachments.

The *identity* of these entities is a composite key — `(case_id, id)` for tasks
and attachments, `(case_id, task_id, id)` for worklogs — where each `id` is a
small per-scope integer allocated by a counter column on the parent (see
`app.crud._seq`). The display strings below are derived from that key, never
stored, so they cannot drift from the source of truth.

    task        T-{case_id}-{id}              e.g. T-1234-1
    worklog     TL-{case_id}-{task_id}-{id}   e.g. TL-1234-1-1
    attachment  A-{case_id}-{id}              e.g. A-1234-3

`parse_*` are the inverse, for inbound display strings (search/filter inputs).
Route path segments carry the raw integers, so handlers don't parse strings.
"""


def format_task_id(case_id: int, id: int) -> str:
    return f"T-{case_id}-{id}"


def format_log_id(case_id: int, task_id: int, id: int) -> str:
    return f"TL-{case_id}-{task_id}-{id}"


def format_attachment_id(case_id: int, id: int) -> str:
    return f"A-{case_id}-{id}"


def _parse(value: str, prefix: str, parts: int) -> tuple[int, ...]:
    if not value.startswith(prefix):
        raise ValueError(f"{value!r} is not a {prefix}… id")
    body = value.removeprefix(prefix)
    pieces = body.split("-")
    if len(pieces) != parts or not all(p.isdecimal() for p in pieces):
        raise ValueError(f"{value!r} is not a well-formed {prefix}… id")
    return tuple(int(p) for p in pieces)


def parse_task_id(value: str) -> tuple[int, int]:
    """'T-1234-1' -> (1234, 1)."""
    case_id, id = _parse(value, "T-", 2)
    return case_id, id


def parse_log_id(value: str) -> tuple[int, int, int]:
    """'TL-1234-1-2' -> (1234, 1, 2)."""
    case_id, task_id, id = _parse(value, "TL-", 3)
    return case_id, task_id, id


def parse_attachment_id(value: str) -> tuple[int, int]:
    """'A-1234-3' -> (1234, 3)."""
    case_id, id = _parse(value, "A-", 2)
    return case_id, id
