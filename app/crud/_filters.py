"""Shared building blocks for the list views' clause-based filters.

Every list endpoint (cases, tasks, alerts, observables) takes repeated
`filter=key~op~value` query params. Terms are OR'd within a key and AND'd across
keys. This module owns the wire parsing and the tag-key grouping; each resource
supplies its own key→SQL mapping for its columns.
"""

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import String, and_, cast, false, or_
from sqlmodel import select

from app.models.tag import Tag, Tagging, TaggableType

#: Wire prefix marking a tag-key clause (e.g. "tag:tlp", "tag:kill-chain:phase").
TAG_PREFIX = "tag:"


@dataclass(frozen=True)
class FilterClause:
    """One `key op value` filter term. `op` is "eq" (exact) or "co" (contains)."""

    key: str
    op: str
    value: str

    @property
    def contains(self) -> bool:
        return self.op == "co"


def parse_clauses(raw_filters: list[str] | None) -> tuple[FilterClause, ...]:
    """Parse repeated `key~op~value` params into clauses, dropping malformed
    terms (bad arity, unknown op, empty key/value)."""
    clauses = []
    for raw in raw_filters or ():
        parts = raw.split("~", 2)
        if len(parts) != 3:
            continue
        key, op, value = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if op not in ("eq", "co") or not key or not value:
            continue
        clauses.append(FilterClause(key=key, op=op, value=value))
    return tuple(clauses)


def group_by_key(clauses: tuple[FilterClause, ...]) -> dict[str, list[FilterClause]]:
    """Bucket clauses by key, preserving order, for OR-within / AND-across."""
    groups: dict[str, list[FilterClause]] = defaultdict(list)
    for clause in clauses:
        groups[clause.key].append(clause)
    return groups


def enum_condition(col, enum_cls: type[Enum], clause: FilterClause):
    """Condition for a native-enum column. Values must be resolved to enum
    members in Python — binding a raw string raises at the driver, and ilike
    doesn't compile against a Postgres enum type. An unmatched value yields
    `false()` (the clause matches nothing) rather than an error."""
    if clause.contains:
        needle = clause.value.lower()
        matches = [m for m in enum_cls if needle in m.value.lower()]
        return col.in_(matches) if matches else false()
    try:
        return col == enum_cls(clause.value)
    except ValueError:
        return false()


# --- tag-key grouping ------------------------------------------------------
# Tags are (namespace, predicate, value) triples. Filtering groups them into
# value-aware keys: OR within a key, AND across keys. Free tags (no namespace)
# are not filterable.


def tag_group_key(namespace: str, predicate: str, value: str) -> str | None:
    """The filter key a tag groups under. Free tags return None.

        ("tlp", "amber", "")            -> "tlp"             (predicate is the value)
        ("kill-chain", "phase", "exp")  -> "kill-chain:phase"
        ("", "phishing", "")            -> None              (free tag, excluded)
    """
    if not namespace:
        return None
    return f"{namespace}:{predicate}" if value else namespace


def tag_group_value(namespace: str, predicate: str, value: str) -> str:
    """The facet value shown under a tag key (the value, or the predicate for
    two-part tags like tlp:amber where the predicate *is* the value)."""
    return value or predicate


def tag_key_to_identity(tag_key: str, value: str) -> tuple[str, str, str]:
    """Inverse of `tag_group_key`: reconstruct the (namespace, predicate, value)
    identity from a group key + selected value. A colon in the key marks the
    value-present shape; otherwise the key is the namespace and value the
    predicate."""
    if ":" in tag_key:
        namespace, predicate = tag_key.split(":", 1)
        return namespace, predicate, value
    return tag_key, value, ""


def tag_key_condition(taggable_type: TaggableType, id_column, tag_key: str, clauses):
    """A WHERE condition: the entity carries at least one of this key's OR-ed
    tag values. `id_column` is the entity's PK, matched against Tagging (whose
    taggable_id is stored as a string)."""
    conds = []
    for c in clauses:
        ns, pred, val = tag_key_to_identity(tag_key, c.value)
        conds.append(
            and_(Tag.namespace == ns, Tag.predicate == pred, Tag.value == val)
        )
    tagged = (
        select(Tagging.taggable_id)
        .join(Tag, Tag.id == Tagging.tag_id)
        .where(Tagging.taggable_type == taggable_type, or_(*conds))
    )
    return cast(id_column, String).in_(tagged)


def group_tag_facets(tag_rows) -> dict[str, list[str]]:
    """Group distinct Tag rows into filterable keys → sorted values, dropping
    free tags. Powers a resource's tag-key filter dropdowns."""
    values: dict[str, set[str]] = defaultdict(set)
    for t in tag_rows:
        key = tag_group_key(t.namespace, t.predicate, t.value)
        if key is None:
            continue
        values[key].add(tag_group_value(t.namespace, t.predicate, t.value))
    return {k: sorted(values[k]) for k in sorted(values)}
