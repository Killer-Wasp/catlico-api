from dataclasses import dataclass

from sqlalchemy import String, cast, false, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._filters import (
    TAG_PREFIX,
    FilterClause,
    enum_condition,
    group_by_key,
    group_tag_facets,
    parse_clauses,
    tag_key_condition,
)
from app.crud.pagination import paginate
from app.models.case_ import Case, CaseListFacets, CaseStatus
from app.models.case_share import CaseShare
from app.models.tag import Tag, Tagging, TaggableType
from app.models.user import User

# Sort key -> orderable column. "updated" coalesces to created_at so cases that
# were never updated still sort sensibly. Anything else falls back to id.
_SORT_COLUMNS = {
    "id": Case.id,
    "created": Case.created_at,
    "updated": func.coalesce(Case.updated_at, Case.created_at),
}

#: Assignee filter sentinel meaning "no assignee" (assignee_id IS NULL). Mirrors
#: the UI's "Unassigned" token, which can be selected alongside real assignees.
UNASSIGNED = "Unassigned"


@dataclass(frozen=True)
class CaseListFilter:
    """Server-side filter + sort spec for the case list. Clauses are grouped by
    key: OR within a key, AND across keys — e.g. (status=Open OR status=Resolved)
    AND (title contains "phish"). No clauses imposes no constraint."""

    clauses: tuple[FilterClause, ...] = ()
    sort: str = "id"
    order: str = "desc"

    @classmethod
    def from_query(
        cls,
        raw_filters: list[str] | None = None,
        *,
        sort: str = "id",
        order: str = "desc",
    ) -> "CaseListFilter":
        return cls(clauses=parse_clauses(raw_filters), sort=sort, order=order)


def _core_clause_cond(key: str, clause: FilterClause):
    """SQL condition for a non-tag key, or None for an unknown key (ignored).
    A known key with an unusable value yields false() — it matches nothing
    rather than silently dropping the constraint."""
    v = clause.value
    contains = clause.contains
    if key == "status":
        return enum_condition(Case.status, CaseStatus, clause)
    if key == "severity":
        if contains:
            return cast(Case.severity, String).ilike(f"%{v}%")
        try:
            return Case.severity == int(v)
        except ValueError:
            return false()
    if key == "assignee":
        if v == UNASSIGNED:
            return Case.assignee_id.is_(None)
        email_cond = User.email.ilike(f"%{v}%") if contains else User.email == v
        return Case.assignee_id.in_(select(User.id).where(email_cond))
    if key == "title":
        return Case.title.ilike(f"%{v}%") if contains else Case.title == v
    if key == "case":
        id_str = cast(Case.id, String)
        vv = v.lstrip("#")
        return id_str.ilike(f"%{vv}%") if contains else id_str == vv
    return None


def _apply_case_filters(stmt, f: CaseListFilter):
    for key, clauses in group_by_key(f.clauses).items():
        if key.startswith(TAG_PREFIX):
            stmt = stmt.where(
                tag_key_condition(
                    TaggableType.case, Case.id, key[len(TAG_PREFIX) :], clauses
                )
            )
            continue
        conds = [c for c in (_core_clause_cond(key, cl) for cl in clauses) if c is not None]
        if conds:
            stmt = stmt.where(or_(*conds))
    return stmt


def _case_order_by(f: CaseListFilter):
    col = _SORT_COLUMNS.get(f.sort, Case.id)
    direction = col.asc() if f.order == "asc" else col.desc()
    return direction, Case.id.desc()


async def list_cases_for_org(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
    filters: CaseListFilter | None = None,
) -> tuple[list[Case], int]:
    filters = filters or CaseListFilter()
    base = (
        select(Case)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
        )
    )
    base = _apply_case_filters(base, filters)

    return await paginate(
        session, base, *_case_order_by(filters), skip=skip, limit=limit
    )


async def case_list_facets(
    session: AsyncSession, organisation_id: str
) -> CaseListFacets:
    """Distinct assignee emails and tag strings present on the org's (non-deleted)
    cases, plus whether any case is unassigned. Powers the list view's filter
    dropdowns so they offer values across the whole result set, not just one page."""
    org_cases = (
        select(Case.id, Case.assignee_id)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            CaseShare.organisation_id == organisation_id,
            Case.deleted_at.is_(None),
        )
        .subquery()
    )

    emails = list(
        (
            await session.execute(
                select(User.email)
                .join(org_cases, org_cases.c.assignee_id == User.id)
                .distinct()
                .order_by(User.email)
            )
        ).scalars()
    )
    unassigned = bool(
        (
            await session.execute(
                select(func.count())
                .select_from(org_cases)
                .where(org_cases.c.assignee_id.is_(None))
            )
        ).scalar_one()
    )
    tag_rows = (
        (
            await session.execute(
                select(Tag)
                .join(Tagging, Tagging.tag_id == Tag.id)
                .join(org_cases, cast(org_cases.c.id, String) == Tagging.taggable_id)
                .where(Tagging.taggable_type == TaggableType.case)
                .distinct()
                .order_by(Tag.namespace, Tag.predicate, Tag.value)
            )
        )
        .scalars()
        .all()
    )
    return CaseListFacets(
        assignees=emails,
        unassigned=unassigned,
        tag_keys=group_tag_facets(tag_rows),
    )
