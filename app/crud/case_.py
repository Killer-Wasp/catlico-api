import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, and_, cast, delete, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.models.alert import Alert
from app.models.case_ import (
    Case,
    CaseCreate,
    CaseListFacets,
    CaseResolutionStatus,
    CaseStatus,
    CaseUpdate,
)
from app.models.case_merge import CaseMerge
from app.models.case_share import CaseShare
from app.models.comment import Comment, CommentEntityType
from app.models.enrichment import EnrichmentJob, ReportTag
from app.models.flag import Flag, FlagEntityType
from app.models.log import Log
from app.models.observable import Observable, ObservableShare
from app.models.tag import Tag, Tagging, TaggableType, parse_tag, tag_to_string
from app.models.task import Task
from app.models.user import User


class MergeError(Exception):
    """Raised by merge_cases on a validation failure. Carries an HTTP status so the
    route layer can translate it without re-deriving intent."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def get_case(session: AsyncSession, case_id: int) -> Case | None:
    """Returns the case only if it exists and is not soft-deleted."""
    case = await session.get(Case, case_id)
    if case is None or case.deleted_at is not None:
        return None
    return case


# Sort key → orderable column. "updated" coalesces to created_at so cases that
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
    """Server-side filter + sort spec for the case list. Every collection is
    OR-within / AND-across: e.g. (status in {Open, Resolved}) AND (severity in
    {3, 4}). Empty collections impose no constraint."""

    statuses: tuple[str, ...] = ()
    severities: tuple[int, ...] = ()
    #: Assignee emails to match (resolved to user ids via a subquery).
    assignee_emails: tuple[str, ...] = ()
    #: Also match unassigned cases (assignee_id IS NULL).
    include_unassigned: bool = False
    #: Tag strings; a case matches if it carries any of them.
    tags: tuple[str, ...] = ()
    #: Case-insensitive title substrings; a case matches any.
    titles: tuple[str, ...] = ()
    #: Case-number substrings matched against the (stringified) id.
    case_queries: tuple[str, ...] = ()
    sort: str = "id"
    order: str = "desc"

    @classmethod
    def from_params(
        cls,
        *,
        statuses: list[str] | None = None,
        severities: list[int] | None = None,
        assignees: list[str] | None = None,
        tags: list[str] | None = None,
        titles: list[str] | None = None,
        case_queries: list[str] | None = None,
        sort: str = "id",
        order: str = "desc",
    ) -> "CaseListFilter":
        """Build a filter from raw query params, splitting the `assignees` list
        into real emails and the `Unassigned` sentinel."""
        assignees = assignees or []
        emails = tuple(a for a in assignees if a != UNASSIGNED)
        return cls(
            statuses=tuple(statuses or ()),
            severities=tuple(severities or ()),
            assignee_emails=emails,
            include_unassigned=UNASSIGNED in assignees,
            tags=tuple(tags or ()),
            titles=tuple(t for t in (titles or ()) if t.strip()),
            case_queries=tuple(
                c.lstrip("#").strip() for c in (case_queries or []) if c.strip()
            ),
            sort=sort,
            order=order,
        )


def _apply_case_filters(stmt, f: CaseListFilter):
    if f.statuses:
        stmt = stmt.where(Case.status.in_(f.statuses))
    if f.severities:
        stmt = stmt.where(Case.severity.in_(f.severities))

    assignee_conds = []
    if f.assignee_emails:
        assignee_conds.append(
            Case.assignee_id.in_(
                select(User.id).where(User.email.in_(f.assignee_emails))
            )
        )
    if f.include_unassigned:
        assignee_conds.append(Case.assignee_id.is_(None))
    if assignee_conds:
        stmt = stmt.where(or_(*assignee_conds))

    if f.tags:
        tag_conds = []
        for raw in f.tags:
            try:
                ns, pred, val = parse_tag(raw)
            except ValueError:
                continue
            tag_conds.append(
                and_(Tag.namespace == ns, Tag.predicate == pred, Tag.value == val)
            )
        if tag_conds:
            tagged = (
                select(Tagging.taggable_id)
                .join(Tag, Tag.id == Tagging.tag_id)
                .where(Tagging.taggable_type == TaggableType.case, or_(*tag_conds))
            )
            stmt = stmt.where(cast(Case.id, String).in_(tagged))

    if f.titles:
        stmt = stmt.where(or_(*[Case.title.ilike(f"%{q}%") for q in f.titles]))
    if f.case_queries:
        stmt = stmt.where(
            or_(*[cast(Case.id, String).ilike(f"%{q}%") for q in f.case_queries])
        )
    return stmt


def _apply_case_order(stmt, f: CaseListFilter):
    col = _SORT_COLUMNS.get(f.sort, Case.id)
    direction = col.asc() if f.order == "asc" else col.desc()
    # Tie-break on id (desc) so pages stay stable when the sort column has ties.
    return stmt.order_by(direction, Case.id.desc())


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

    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = _apply_case_order(base, filters).offset(skip).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


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
        tags=[tag_to_string(t) for t in tag_rows],
    )


async def create_case(
    session: AsyncSession,
    case_in: CaseCreate,
    *,
    owner_org_id: str,
    owner_role_id: uuid.UUID,
    created_by: str,
) -> Case:
    case = Case(
        title=case_in.title,
        description=case_in.description,
        severity=case_in.severity,
        tlp=case_in.tlp,
        pap=case_in.pap,
        assignee_id=case_in.assignee_id,
        start_date=case_in.start_date,
        summary=case_in.summary,
        created_by=created_by,
    )
    session.add(case)
    await session.flush()
    share = CaseShare(
        case_id=case.id,
        organisation_id=owner_org_id,
        role_id=owner_role_id,
        is_owner=True,
        created_by=created_by,
    )
    session.add(share)
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor=created_by,
        details={
            "title": case.title,
            "severity": case.severity,
            "tlp": case.tlp,
            "pap": case.pap,
        },
    )
    return case


async def update_case(
    session: AsyncSession, case: Case, case_in: CaseUpdate, updated_by: str
) -> Case:
    update_data = case_in.model_dump(exclude_unset=True)
    changes = {
        field: [getattr(case, field, None), new]
        for field, new in update_data.items()
        if getattr(case, field, None) != new
    }
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    case.sqlmodel_update(update_data)
    session.add(case)
    await session.flush()
    if changes:
        await record_audit(
            session,
            action="update",
            obj=case,
            context=case,
            actor=updated_by,
            details=changes,
        )
    return case


async def delete_case(session: AsyncSession, case: Case, deleted_by: str) -> None:
    """Soft delete: flag the case and cascade the flag across the whole investigation
    — its tasks, those tasks' logs, its observables, and its comments. Mirrors
    delete_task's task->log cascade, widened to the case scope. CaseShare rows are
    left intact; reads exclude the case via its deleted_at, so they never surface it."""
    now = datetime.now(UTC)
    case.deleted_at = now
    case.deleted_by = deleted_by
    session.add(case)

    await session.execute(
        update(Task)
        .where(Task.case_id == case.id, Task.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Log)
        .where(
            Log.task_id.in_(select(Task.id).where(Task.case_id == case.id)),
            Log.deleted_at.is_(None),
        )
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Observable)
        .where(Observable.case_id == case.id, Observable.deleted_at.is_(None))
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.execute(
        update(Comment)
        .where(
            Comment.entity_type == CommentEntityType.case,
            Comment.entity_id == str(case.id),
            Comment.deleted_at.is_(None),
        )
        .values(deleted_at=now, deleted_by=deleted_by)
    )
    await session.flush()
    await record_audit(
        session, action="delete", obj=case, context=case, actor=deleted_by
    )


async def lineage_for_many(
    session: AsyncSession, case_ids: list[int]
) -> dict[int, tuple[int | None, list[int]]]:
    """Batched merge lineage for a page of cases: {case_id: (merged_into, merged_from)}.

    One query over case_merge, never per-row — most pages have no merged cases and
    this returns nothing. `merged_into` = the successor this case was merged into;
    `merged_from` = the source cases this (new) case absorbed."""
    if not case_ids:
        return {}
    rows = (
        await session.execute(
            select(CaseMerge.source_case_id, CaseMerge.target_case_id).where(
                CaseMerge.source_case_id.in_(case_ids)
                | CaseMerge.target_case_id.in_(case_ids)
            )
        )
    ).all()
    wanted = set(case_ids)
    out: dict[int, tuple[int | None, list[int]]] = {cid: (None, []) for cid in case_ids}
    for source_id, target_id in rows:
        if source_id in wanted:
            into, frm = out[source_id]
            out[source_id] = (target_id, frm)
        if target_id in wanted:
            into, frm = out[target_id]
            out[target_id] = (into, [*frm, source_id])
    return out


async def _reparent_observables(
    session: AsyncSession, source_ids: list[int], new_case_id: int, actor: str
) -> int:
    """Move source observables onto the new case, deduping on (type, data) which the
    uq_observable_case_dedup partial index forbids duplicating. Survivor wins by
    union: ioc/sighted OR, ignore_similarity AND, tlp max, messages concatenated;
    enrichments/report-tags/shares reassigned off the twin, then the twin soft-deleted."""
    obs_list = (
        (
            await session.execute(
                select(Observable)
                .where(
                    Observable.case_id.in_(source_ids),
                    Observable.deleted_at.is_(None),
                )
                .order_by(Observable.created_at, Observable.id)
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    survivors: dict[tuple[str, str], Observable] = {}
    moved = 0
    for obs in obs_list:
        key = (obs.observable_type, obs.data)
        survivor = survivors.get(key)
        if survivor is None:
            obs.case_id = new_case_id
            session.add(obs)
            survivors[key] = obs
            moved += 1
            continue
        # Collision: fold the twin into the survivor.
        survivor.ioc = survivor.ioc or obs.ioc
        survivor.sighted = survivor.sighted or obs.sighted
        survivor.ignore_similarity = survivor.ignore_similarity and obs.ignore_similarity
        survivor.tlp = max(survivor.tlp, obs.tlp)
        if obs.message and obs.message not in (survivor.message or ""):
            survivor.message = (
                f"{survivor.message}\n{obs.message}".strip()
                if survivor.message
                else obs.message
            )
        session.add(survivor)
        await session.execute(
            update(EnrichmentJob)
            .where(EnrichmentJob.observable_id == obs.id)
            .values(observable_id=survivor.id)
        )
        await session.execute(
            update(ReportTag)
            .where(ReportTag.observable_id == obs.id)
            .values(observable_id=survivor.id)
        )
        await _union_observable_shares(session, obs.id, survivor.id)
        obs.deleted_at = now
        obs.deleted_by = actor
        session.add(obs)
    return moved


async def _union_observable_shares(
    session: AsyncSession, twin_id: uuid.UUID, survivor_id: uuid.UUID
) -> None:
    twin_shares = (
        (
            await session.execute(
                select(ObservableShare).where(ObservableShare.observable_id == twin_id)
            )
        )
        .scalars()
        .all()
    )
    if not twin_shares:
        return
    existing = set(
        (
            await session.execute(
                select(ObservableShare.organisation_id).where(
                    ObservableShare.observable_id == survivor_id
                )
            )
        )
        .scalars()
        .all()
    )
    for share in twin_shares:
        if share.organisation_id not in existing:
            session.add(
                ObservableShare(
                    observable_id=survivor_id,
                    organisation_id=share.organisation_id,
                    created_by=share.created_by,
                )
            )
            existing.add(share.organisation_id)
        await session.delete(share)


async def merge_cases(
    session: AsyncSession,
    *,
    source_ids: list[int],
    case_in: CaseCreate,
    owner_org_id: str,
    owner_role_id: uuid.UUID,
    actor: str,
) -> Case:
    """Merge 2+ same-owner-org cases into a fresh case (create-new model). Sources are
    frozen as Duplicated and read-only; their children are reparented (moved) onto the
    new case; lineage is recorded in case_merge. See docs/case-merge-design.md.

    Runs entirely in the caller's transaction. Raises MergeError on any validation
    failure (aborting the whole merge)."""
    distinct_ids = sorted(set(source_ids))
    if len(distinct_ids) < 2:
        raise MergeError(400, "Merge requires at least 2 distinct cases")

    # Lock sources in a deterministic order (id asc) — deadlock-free across overlapping
    # merges; on Postgres this also blocks concurrent child inserts.
    sources = (
        (
            await session.execute(
                select(Case)
                .where(Case.id.in_(distinct_ids))
                .order_by(Case.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    by_id = {c.id: c for c in sources}
    for cid in distinct_ids:
        case = by_id.get(cid)
        if case is None or case.deleted_at is not None:
            raise MergeError(404, f"Case {cid} not found")
        if case.status == CaseStatus.duplicated:
            raise MergeError(409, f"Case {cid} is already merged")

    # Ownership: every source must be owned by the acting org (enforces single-org v1).
    owner_rows = (
        await session.execute(
            select(CaseShare.case_id, CaseShare.organisation_id).where(
                CaseShare.case_id.in_(distinct_ids),
                CaseShare.is_owner.is_(True),
            )
        )
    ).all()
    owner_by_case = {cid: org for cid, org in owner_rows}
    for cid in distinct_ids:
        if owner_by_case.get(cid) != owner_org_id:
            raise MergeError(
                403, "All source cases must be owned by your organisation"
            )

    # TLP/PAP floor: the new case must be at least as restrictive as every source.
    max_tlp = max(c.tlp for c in sources)
    max_pap = max(c.pap for c in sources)
    if case_in.tlp < max_tlp or case_in.pap < max_pap:
        raise MergeError(
            422, "Merged case TLP/PAP cannot be less restrictive than its sources"
        )

    # Create the new (survivor) case + its owner share. No create audit — the merge
    # audit below is the single main_action event.
    new_case = Case(
        title=case_in.title,
        description=case_in.description,
        severity=case_in.severity,
        tlp=case_in.tlp,
        pap=case_in.pap,
        assignee_id=case_in.assignee_id,
        start_date=case_in.start_date,
        summary=case_in.summary,
        created_by=actor,
    )
    session.add(new_case)
    await session.flush()
    session.add(
        CaseShare(
            case_id=new_case.id,
            organisation_id=owner_org_id,
            role_id=owner_role_id,
            is_owner=True,
            created_by=actor,
        )
    )
    await session.flush()

    source_id_strs = [str(cid) for cid in distinct_ids]

    # Tasks (+ their logs / task_share follow the task automatically).
    task_res = await session.execute(
        update(Task)
        .where(Task.case_id.in_(distinct_ids), Task.deleted_at.is_(None))
        .values(case_id=new_case.id)
    )
    # Observables (with dedup).
    moved_obs = await _reparent_observables(
        session, distinct_ids, new_case.id, actor
    )
    # Case comments (own UUID PK — no collision).
    comment_res = await session.execute(
        update(Comment)
        .where(
            Comment.entity_type == CommentEntityType.case,
            Comment.entity_id.in_(source_id_strs),
            Comment.deleted_at.is_(None),
        )
        .values(entity_id=str(new_case.id))
    )
    # Alerts — import lineage follows the survivor.
    alert_res = await session.execute(
        update(Alert)
        .where(Alert.case_id.in_(distinct_ids), Alert.deleted_at.is_(None))
        .values(case_id=new_case.id)
    )
    # Tags — dedup the union (PK includes the case id).
    tag_ids = list(
        (
            await session.execute(
                select(Tagging.tag_id)
                .where(
                    Tagging.taggable_type == TaggableType.case,
                    Tagging.taggable_id.in_(source_id_strs),
                )
                .distinct()
            )
        ).scalars()
    )
    await session.execute(
        delete(Tagging).where(
            Tagging.taggable_type == TaggableType.case,
            Tagging.taggable_id.in_(source_id_strs),
        )
    )
    for tag_id in tag_ids:
        session.add(
            Tagging(
                tag_id=tag_id,
                taggable_type=TaggableType.case,
                taggable_id=str(new_case.id),
            )
        )
    # Flags — collapse to one row per org.
    flag_orgs = list(
        (
            await session.execute(
                select(Flag.organisation_id)
                .where(
                    Flag.entity_type == FlagEntityType.case,
                    Flag.entity_id.in_(source_id_strs),
                )
                .distinct()
            )
        ).scalars()
    )
    await session.execute(
        delete(Flag).where(
            Flag.entity_type == FlagEntityType.case,
            Flag.entity_id.in_(source_id_strs),
        )
    )
    for org in flag_orgs:
        session.add(
            Flag(
                entity_type=FlagEntityType.case,
                entity_id=str(new_case.id),
                organisation_id=org,
                created_by=actor,
            )
        )

    # Secondary (non-owner) shares are NOT carried onto the new case — record them so
    # the owner can deliberately re-share (unioning would be a silent org-boundary leak).
    secondary_orgs = sorted(
        {
            org
            for cid, org in (
                await session.execute(
                    select(CaseShare.case_id, CaseShare.organisation_id).where(
                        CaseShare.case_id.in_(distinct_ids),
                        CaseShare.is_owner.is_(False),
                    )
                )
            ).all()
        }
    )

    # Freeze sources + record lineage.
    now = datetime.now(UTC)
    for case in sources:
        case.status = CaseStatus.duplicated
        case.resolution_status = CaseResolutionStatus.duplicated
        case.end_date = now
        case.updated_at = now
        case.updated_by = actor
        session.add(case)
        session.add(
            CaseMerge(
                source_case_id=case.id,
                target_case_id=new_case.id,
                created_by=actor,
            )
        )
    await session.flush()

    moved = {
        "tasks": task_res.rowcount or 0,
        "observables": moved_obs,
        "comments": comment_res.rowcount or 0,
        "alerts": alert_res.rowcount or 0,
        "tags": len(tag_ids),
        "flags": len(flag_orgs),
    }
    await record_audit(
        session,
        action="merge",
        obj=new_case,
        context=new_case,
        actor=actor,
        details={
            "sources": distinct_ids,
            "moved": moved,
            "shares_not_carried": secondary_orgs,
        },
    )
    for case in sources:
        await record_audit(
            session,
            action="merge",
            obj=case,
            context=case,
            actor=actor,
            main_action=False,
            details={"merged_into": new_case.id},
        )
    return new_case
