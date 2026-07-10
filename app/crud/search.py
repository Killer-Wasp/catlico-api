"""Global search: query classification and per-entity federated queries.

One query per entity type, each reusing that type's existing visibility
predicate — search must never return a row the corresponding list view would
hide. Spec: docs/global-search-design.md (catlico workspace root).
"""

import ipaddress
from dataclasses import dataclass

from sqlalchemy import Text, cast, func


@dataclass(frozen=True)
class ClassifiedQuery:
    """How the raw query string should be matched.

    net is a normalized address/CIDR string when the query parses as one
    (drives inet matching on observables); other entity types always get the
    text treatment regardless.
    """

    text: str
    net: str | None
    is_bare_ip: bool


def classify_query(q: str) -> ClassifiedQuery:
    raw = q.strip()
    try:
        return ClassifiedQuery(text=raw, net=str(ipaddress.ip_address(raw)), is_bare_ip=True)
    except ValueError:
        pass
    if "/" in raw:
        try:
            return ClassifiedQuery(
                text=raw, net=str(ipaddress.ip_network(raw, strict=False)), is_bare_ip=False
            )
        except ValueError:
            pass
    return ClassifiedQuery(text=raw, net=None, is_bare_ip=False)


def prefix_tsquery(q: str):
    """tsquery from user input, prefix-matching the last token so typing feels
    live. websearch_to_tsquery never raises on any input (the query string is
    data, not syntax); its text form always ends in a quoted token, so
    appending ':*' is valid. Empty tsquery -> NULL -> matches nothing.
    """
    base_txt = cast(func.websearch_to_tsquery("simple", q), Text)
    return func.to_tsquery("simple", func.nullif(base_txt, "") + ":*")


def like_pattern(q: str) -> str:
    """%q% with LIKE wildcards escaped; use with .ilike(pattern, escape="\\\\")."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
