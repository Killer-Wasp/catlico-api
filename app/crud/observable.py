import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, false, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.crud._filters import FilterClause, group_by_key, parse_clauses
from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.crud.case_share import list_non_owner_org_ids
from app.crud.organisation_link import get_link
from app.models.alert import Alert
from app.models.case_ import Case
from app.models.case_share import CaseShare
from app.models.case_status import CaseStage, CaseStatus
from app.models.observable import (
    Observable,
    ObservableCreate,
    ObservableFacets,
    ObservableShare,
    ObservableType,
    ObservableTypeCreate,
    ObservableUpdate,
)
from app.models.organisation_link import AutoShareMode


def ip_value(observable_type: str, data: str) -> str | None:
    """Normalized inet value for an ip-typed observable: a host address or a
    network (accepts prefix and dotted-netmask forms). None when not ip-typed
    or unparseable — search simply won't range-match that row."""
    if observable_type != "ip":
        return None
    raw = data.strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    try:
        return str(ipaddress.ip_network(raw, strict=False))
    except ValueError:
        return None


# Raw observable_type (lower-cased) → UI category. Mirrors the web TYPE_MAP;
# unknown raws fall into "other".
_TYPE_TO_CATEGORY = {
    "domain": "domain",
    "url": "url",
    "mail": "mail",
    "email": "mail",
    "ip": "ip",
    "ipv4": "ip",
    "ipv6": "ip",
    "hash": "hash",
    "file": "file",
    "filename": "file",
    "other": "other",
}
#: Category → its raw observable_type values, for the reverse (filter) direction.
_CATEGORY_TO_RAWS: dict[str, list[str]] = {}
for _raw, _cat in _TYPE_TO_CATEGORY.items():
    _CATEGORY_TO_RAWS.setdefault(_cat, []).append(_raw)
#: Raws mapped to a concrete (non-"other") category — everything else is "other".
_NON_OTHER_RAWS = [r for r, c in _TYPE_TO_CATEGORY.items() if c != "other"]

_OBSERVABLE_SORT_COLUMNS = {
    "value": Observable.data,
    "added": Observable.created_at,
}


def observable_source(case_id: int | None, alert_id: int | None) -> str:
    """The UI 'source' of an observable: its case, else its alert, else 'feed'.
    Mirrors the web toObservable() derivation."""
    if case_id is not None:
        return f"#{case_id}"
    if alert_id is not None:
        return f"AL-{alert_id}"
    return "feed"


@dataclass(frozen=True)
class ObservableListFilter:
    """Clause filter + sort for the observable list (OR-within / AND-across).
    Keys: type, tlp, flag, source, value."""

    clauses: tuple[FilterClause, ...] = ()
    sort: str = ""
    order: str = "desc"

    @classmethod
    def from_query(
        cls, raw_filters: list[str] | None = None, *, sort="", order="desc"
    ) -> "ObservableListFilter":
        return cls(clauses=parse_clauses(raw_filters), sort=sort, order=order)


def _observable_clause_cond(key: str, clause: FilterClause):
    """Condition for one clause, None for an unknown key (ignored). A known key
    with an unusable value yields false() — matches nothing, never widens."""
    v = clause.value
    contains = clause.contains
    if key == "type":
        # `v` is a UI category; match the raw observable_type(s) behind it.
        col = func.lower(Observable.observable_type)
        if v == "other":
            return col.notin_(_NON_OTHER_RAWS)
        raws = _CATEGORY_TO_RAWS.get(v)
        return col.in_(raws) if raws else false()
    if key == "tlp":
        try:
            return Observable.tlp == int(v)
        except ValueError:
            return false()
    if key == "flag":
        if v == "ioc":
            return Observable.ioc.is_(True)
        if v == "sighted":
            return Observable.sighted.is_(True)
        return false()
    if key == "source":
        if v == "feed":
            return and_(Observable.case_id.is_(None), Observable.alert_id.is_(None))
        if v.startswith("AL-"):
            try:
                return Observable.alert_id == int(v[len("AL-") :])
            except ValueError:
                return false()
        try:
            return Observable.case_id == int(v.lstrip("#"))
        except ValueError:
            return false()
    if key == "value":
        return Observable.data.ilike(f"%{v}%") if contains else Observable.data == v
    return None


def _apply_observable_filters(stmt, f: ObservableListFilter):
    for key, clauses in group_by_key(f.clauses).items():
        conds = [
            c
            for c in (_observable_clause_cond(key, cl) for cl in clauses)
            if c is not None
        ]
        if conds:
            stmt = stmt.where(or_(*conds))
    return stmt


def _observable_order_by(f: ObservableListFilter):
    col = _OBSERVABLE_SORT_COLUMNS.get(f.sort)
    if col is None:
        return (Observable.created_at.desc(),)
    direction = col.asc() if f.order == "asc" else col.desc()
    return direction, Observable.created_at.desc()


def _visible_observable_condition(organisation_id: str):
    """Visibility predicate shared by the org list and its facets."""
    owner_case_ids = select(CaseShare.case_id).where(
        CaseShare.organisation_id == organisation_id,
        CaseShare.is_owner == True,  # noqa: E712
    )
    shared_observable_ids = select(ObservableShare.observable_id).where(
        ObservableShare.organisation_id == organisation_id
    )
    owned_alert_ids = select(Alert.id).where(
        Alert.organisation_id == organisation_id,
        Alert.deleted_at.is_(None),
    )
    return or_(
        Observable.case_id.in_(owner_case_ids),
        Observable.id.in_(shared_observable_ids),
        Observable.alert_id.in_(owned_alert_ids),
    )


def _observable_context(observable: Observable) -> tuple[str | None, str | None]:
    """A case observable's activity-feed context is its case; an alert observable's
    is its alert (only the case context is surfaced by the case feed today)."""
    if observable.case_id is not None:
        return "case", str(observable.case_id)
    if observable.alert_id is not None:
        return "alert", str(observable.alert_id)
    return None, None


async def get_observable(session: AsyncSession, observable_id: uuid.UUID) -> Observable | None:
    obs = await session.get(Observable, observable_id)
    if obs is None or obs.deleted_at is not None:
        return None
    return obs


async def _attachment_meta_map(
    session: AsyncSession, observables: list[Observable]
) -> dict[uuid.UUID, "ObservableAttachmentMeta"]:
    """Map observable_id -> file-attachment metadata for the file observables in the
    list. One query over ObservableAttachmentLink⋈Attachment keyed by observable_id —
    no N+1, regardless of list size. String observables simply aren't in the map."""
    from app.models.attachment import Attachment, ObservableAttachmentLink
    from app.models.observable import ObservableAttachmentMeta

    ids = [o.id for o in observables]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(ObservableAttachmentLink, Attachment)
            .join(Attachment, Attachment.id == ObservableAttachmentLink.attachment_id)
            .where(
                ObservableAttachmentLink.observable_id.in_(ids),
                ObservableAttachmentLink.deleted_at.is_(None),
            )
        )
    ).all()
    return {
        link.observable_id: ObservableAttachmentMeta(
            filename=link.name, size=blob.size, content_type=blob.content_type
        )
        for link, blob in rows
    }


async def to_public_list(
    session: AsyncSession, observables: list[Observable]
) -> list["ObservablePublic"]:
    """Serialize observables to ObservablePublic, populating `.attachment` for file
    observables (null for string observables) via one batched attachment lookup."""
    from app.models.observable import ObservablePublic

    meta = await _attachment_meta_map(session, observables)
    out: list[ObservablePublic] = []
    for o in observables:
        pub = ObservablePublic.model_validate(o, from_attributes=True)
        pub.attachment = meta.get(o.id)
        out.append(pub)
    return out


async def to_public(session: AsyncSession, obs: Observable) -> "ObservablePublic":
    """Single-observable convenience wrapper around to_public_list."""
    (pub,) = await to_public_list(session, [obs])
    return pub


async def get_type(session: AsyncSession, name: str) -> ObservableType | None:
    return await session.get(ObservableType, name)


async def list_types(session: AsyncSession) -> list[ObservableType]:
    result = await session.execute(select(ObservableType).order_by(ObservableType.name))
    return list(result.scalars().all())


async def create_type(
    session: AsyncSession, type_in: ObservableTypeCreate, created_by: str
) -> ObservableType:
    type_ = ObservableType(name=type_in.name, is_attachment=type_in.is_attachment)
    session.add(type_)
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=type_,
        actor=created_by,
        details={"name": type_in.name, "is_attachment": type_in.is_attachment},
    )
    return type_


async def delete_type(
    session: AsyncSession, type_: ObservableType, deleted_by: str
) -> None:
    await session.delete(type_)
    await session.flush()
    await record_audit(
        session,
        action="delete",
        obj=type_,
        actor=deleted_by,
        details={"name": type_.name},
    )


async def check_creatable_type(session: AsyncSession, name: str) -> str | None:
    """Return an error message if this type can't back a string observable, else None.
    Attachment types are rejected until the blob-storage milestone."""
    t = await get_type(session, name)
    if t is None:
        return f"Unknown observable type: {name}"
    if t.is_attachment:
        return f"File observable type '{name}' is not yet supported"
    return None


async def find_case_observable(
    session: AsyncSession, case_id: int, observable_type: str, data: str
) -> Observable | None:
    """Live case observable matching the within-case dedup key, if any."""
    result = await session.execute(
        select(Observable).where(
            Observable.case_id == case_id,
            Observable.observable_type == observable_type,
            Observable.data == data,
            Observable.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_observables_for_case(
    session: AsyncSession,
    case_id: int,
    *,
    organisation_id: str,
    is_owner: bool,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Observable], int]:
    base = select(Observable).where(
        Observable.case_id == case_id, Observable.deleted_at.is_(None)
    )
    if not is_owner:
        base = base.join(
            ObservableShare, ObservableShare.observable_id == Observable.id
        ).where(ObservableShare.organisation_id == organisation_id)

    return await paginate(session, base, Observable.created_at, skip=skip, limit=limit)


async def list_observables_for_alert(
    session: AsyncSession,
    alert_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Observable], int]:
    base = select(Observable).where(
        Observable.alert_id == alert_id, Observable.deleted_at.is_(None)
    )
    return await paginate(session, base, Observable.created_at, skip=skip, limit=limit)


async def similar_cases_for_alert(
    session: AsyncSession,
    alert_id: int,
    *,
    organisation_id: str,
    exclude_case_id: int | None = None,
    limit: int = 20,
) -> list[tuple[Case, int]]:
    """Cases that share at least one observable (same type + value) with the
    alert — the drawer's "Similar cases". Observables flagged `ignore_similarity`
    on either side are excluded, and only cases visible to `organisation_id`
    (via a CaseShare) are returned, ordered by overlap size then recency. Each
    result carries its shared-observable count."""
    alert_pairs = (
        select(
            Observable.observable_type.label("t"),
            Observable.data.label("d"),
        )
        .where(
            Observable.alert_id == alert_id,
            Observable.deleted_at.is_(None),
            Observable.ignore_similarity.is_(False),
        )
        .distinct()
        .subquery()
    )
    case_obs = aliased(Observable)
    overlap = func.count(func.distinct(case_obs.id))
    stmt = (
        select(Case, overlap)
        .join(case_obs, case_obs.case_id == Case.id)
        .join(
            alert_pairs,
            and_(
                case_obs.observable_type == alert_pairs.c.t,
                case_obs.data == alert_pairs.c.d,
            ),
        )
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            Case.deleted_at.is_(None),
            case_obs.deleted_at.is_(None),
            case_obs.ignore_similarity.is_(False),
            CaseShare.organisation_id == organisation_id,
        )
        .group_by(Case.id)
        .order_by(overlap.desc(), Case.id.desc())
        .limit(limit)
    )
    if exclude_case_id is not None:
        stmt = stmt.where(Case.id != exclude_case_id)
    rows = (await session.execute(stmt)).all()
    return [(case, count) for case, count in rows]


async def similar_cases_for_case(
    session: AsyncSession,
    case_id: int,
    *,
    organisation_id: str,
    limit: int = 20,
) -> list[tuple[Case, int]]:
    """Cases that share at least one observable (same type + value) with the
    given case — the case detail's "Similar cases". Mirrors
    `similar_cases_for_alert` with the source flipped to this case's observables.
    The source case itself and merged/duplicated tombstones are excluded;
    observables flagged `ignore_similarity` on either side are ignored, and only
    cases visible to `organisation_id` (via a CaseShare) are returned, ordered by
    overlap size then recency. Each result carries its shared-observable count."""
    source_pairs = (
        select(
            Observable.observable_type.label("t"),
            Observable.data.label("d"),
        )
        .where(
            Observable.case_id == case_id,
            Observable.deleted_at.is_(None),
            Observable.ignore_similarity.is_(False),
        )
        .distinct()
        .subquery()
    )
    case_obs = aliased(Observable)
    overlap = func.count(func.distinct(case_obs.id))
    stmt = (
        select(Case, overlap)
        .join(case_obs, case_obs.case_id == Case.id)
        .join(
            source_pairs,
            and_(
                case_obs.observable_type == source_pairs.c.t,
                case_obs.data == source_pairs.c.d,
            ),
        )
        .join(CaseShare, CaseShare.case_id == Case.id)
        .join(CaseStatus, CaseStatus.id == Case.status_id)
        .where(
            Case.id != case_id,
            Case.deleted_at.is_(None),
            CaseStatus.stage != CaseStage.duplicated,
            case_obs.deleted_at.is_(None),
            case_obs.ignore_similarity.is_(False),
            CaseShare.organisation_id == organisation_id,
        )
        .group_by(Case.id)
        .order_by(overlap.desc(), Case.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(case, count) for case, count in rows]


async def related_cases_for_observable(
    session: AsyncSession,
    observable: Observable,
    *,
    organisation_id: str,
    limit: int = 20,
) -> list[tuple[Case, int]]:
    """Cases that contain this observable's value (same type + value) — the
    observable drawer's "Related cases". Only cases visible to `organisation_id`
    (via a CaseShare) are returned, ordered by case id descending. Each result
    carries the count of matching observables in that case."""
    case_obs = aliased(Observable)
    overlap = func.count(func.distinct(case_obs.id))
    stmt = (
        select(Case, overlap)
        .join(case_obs, case_obs.case_id == Case.id)
        .join(CaseShare, CaseShare.case_id == Case.id)
        .where(
            case_obs.observable_type == observable.observable_type,
            case_obs.data == observable.data,
            case_obs.deleted_at.is_(None),
            Case.deleted_at.is_(None),
            CaseShare.organisation_id == organisation_id,
        )
        .group_by(Case.id)
        .order_by(Case.id.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(case, count) for case, count in rows]


async def list_observables_for_org(
    session: AsyncSession,
    *,
    organisation_id: str,
    skip: int = 0,
    limit: int = 100,
    filters: ObservableListFilter | None = None,
) -> tuple[list[Observable], int]:
    """Every live observable the active org may see, across all its cases and alerts:
    case observables on cases it owns (owner sees all), observables explicitly shared to
    it via ObservableShare, and observables on alerts it owns. Filtered, sorted and
    paginated server-side."""
    filters = filters or ObservableListFilter()
    base = select(Observable).where(
        Observable.deleted_at.is_(None),
        _visible_observable_condition(organisation_id),
    )
    base = _apply_observable_filters(base, filters)
    return await paginate(
        session, base, *_observable_order_by(filters), skip=skip, limit=limit
    )


async def visible_values_for_ids(
    session: AsyncSession,
    *,
    organisation_id: str,
    observable_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[str, str]]:
    """Map observable_id -> (type, data) for the ids the org may see, in one query.
    Rides the same visibility predicate as the org list, so an observable that was
    soft-deleted or whose share was revoked drops out of the map rather than leaking
    its value through a caller that only checked its own scope. Unknown ids are
    absent — callers render them as unresolved rather than erroring."""
    if not observable_ids:
        return {}
    rows = await session.execute(
        select(Observable.id, Observable.observable_type, Observable.data).where(
            Observable.id.in_(observable_ids),
            Observable.deleted_at.is_(None),
            _visible_observable_condition(organisation_id),
        )
    )
    return {row[0]: (row[1], row[2]) for row in rows.all()}


async def observable_facets(
    session: AsyncSession, organisation_id: str
) -> ObservableFacets:
    """Distinct 'source' values (case / alert / feed) across the org's visible
    observables, for the list view's Source filter dropdown."""
    rows = (
        await session.execute(
            select(Observable.case_id, Observable.alert_id)
            .where(
                Observable.deleted_at.is_(None),
                _visible_observable_condition(organisation_id),
            )
            .distinct()
        )
    ).all()
    sources = sorted({observable_source(case_id, alert_id) for case_id, alert_id in rows})
    return ObservableFacets(sources=sources)


async def _fan_out_shares(
    session: AsyncSession,
    observable: Observable,
    *,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> None:
    """Mirror of task share fan-out: for each other org on the case, honour the
    directed organisation_link.observable_sharing=autoShare and create a share row."""
    target_org_ids = await list_non_owner_org_ids(session, case_id)
    owner_orgs = await session.execute(
        select(CaseShare.organisation_id).where(
            CaseShare.case_id == case_id,
            CaseShare.is_owner == True,  # noqa: E712
        )
    )
    candidate_orgs = set(target_org_ids) | set(owner_orgs.scalars().all())
    candidate_orgs.discard(organisation_id)
    for target_org_id in candidate_orgs:
        link = await get_link(session, organisation_id, target_org_id)
        if link is None or link.observable_sharing != AutoShareMode.auto_share:
            continue
        session.add(
            ObservableShare(
                observable_id=observable.id,
                organisation_id=target_org_id,
                created_by=created_by,
            )
        )


async def create_case_observable(
    session: AsyncSession,
    obs_in: ObservableCreate,
    *,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> Observable:
    observable = Observable(
        case_id=case_id,
        observable_type=obs_in.observable_type,
        data=obs_in.data,
        message=obs_in.message,
        tlp=obs_in.tlp,
        ioc=obs_in.ioc,
        sighted=obs_in.sighted,
        ignore_similarity=obs_in.ignore_similarity,
        organisation_id=organisation_id,
        created_by=created_by,
        ip=ip_value(obs_in.observable_type, obs_in.data),
    )
    session.add(observable)
    await session.flush()
    await _fan_out_shares(
        session,
        observable,
        case_id=case_id,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=observable,
        context_type="case",
        context_id=str(case_id),
        actor=created_by,
        details={
            "observable_type": observable.observable_type,
            "data": observable.data,
            "ioc": observable.ioc,
        },
        organisation_id=observable.organisation_id,
    )
    return observable


async def create_alert_observable(
    session: AsyncSession,
    obs_in: ObservableCreate,
    *,
    alert_id: int,
    organisation_id: str,
    created_by: str,
) -> Observable:
    observable = Observable(
        alert_id=alert_id,
        observable_type=obs_in.observable_type,
        data=obs_in.data,
        message=obs_in.message,
        tlp=obs_in.tlp,
        ioc=obs_in.ioc,
        sighted=obs_in.sighted,
        ignore_similarity=obs_in.ignore_similarity,
        organisation_id=organisation_id,
        created_by=created_by,
        ip=ip_value(obs_in.observable_type, obs_in.data),
    )
    session.add(observable)
    await session.flush()
    await record_audit(
        session,
        action="create",
        obj=observable,
        context_type="alert",
        context_id=str(alert_id),
        actor=created_by,
        details={
            "observable_type": observable.observable_type,
            "data": observable.data,
            "ioc": observable.ioc,
        },
        organisation_id=observable.organisation_id,
    )
    return observable


async def import_alert_observables_to_case(
    session: AsyncSession,
    *,
    alert_id: int,
    case_id: int,
    organisation_id: str,
    created_by: str,
) -> int:
    """Copy an alert's observables into the promoted case, deduped by (type, data).
    Returns the count imported. Fan-out shares apply as for any case observable."""
    from app.crud import attachment as attachment_crud

    alert_obs, _ = await list_observables_for_alert(session, alert_id, limit=10_000)
    imported = 0
    for src in alert_obs:
        if await find_case_observable(session, case_id, src.observable_type, src.data):
            continue
        new_obs = await create_case_observable(
            session,
            ObservableCreate(
                observable_type=src.observable_type,
                data=src.data,
                message=src.message,
                tlp=src.tlp,
                ioc=src.ioc,
                sighted=src.sighted,
                ignore_similarity=src.ignore_similarity,
            ),
            case_id=case_id,
            organisation_id=organisation_id,
            created_by=created_by,
        )
        # File observables carry a blob via an ObservableAttachmentLink. The link is
        # keyed by observable_id, so the new case observable needs its own link — but
        # pointing at the SAME content-addressed blob (no re-upload/data copy). Without
        # this, the promoted observable's GET /file 404s.
        src_link = await attachment_crud.first_observable_link(session, src.id)
        if src_link is not None:
            link, _blob = src_link
            await attachment_crud.create_observable_link(
                session,
                attachment_id=link.attachment_id,
                observable_id=new_obs.id,
                name=link.name,
                organisation_id=organisation_id,
                created_by=created_by,
            )
        imported += 1
    return imported


async def update_observable(
    session: AsyncSession, observable: Observable, obs_in: ObservableUpdate, updated_by: str
) -> Observable:
    update_data = obs_in.model_dump(exclude_unset=True)
    changes = {
        field: [getattr(observable, field, None), new]
        for field, new in update_data.items()
        if getattr(observable, field, None) != new
    }
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    observable.sqlmodel_update(update_data)
    session.add(observable)
    await session.flush()
    if changes:
        ctx_type, ctx_id = _observable_context(observable)
        await record_audit(
            session,
            action="update",
            obj=observable,
            context_type=ctx_type,
            context_id=ctx_id,
            actor=updated_by,
            details=changes,
            organisation_id=observable.organisation_id,
        )
    return observable


async def delete_observable(
    session: AsyncSession, observable: Observable, deleted_by: str
) -> None:
    observable.deleted_at = datetime.now(UTC)
    observable.deleted_by = deleted_by
    session.add(observable)
    await session.flush()
    ctx_type, ctx_id = _observable_context(observable)
    await record_audit(
        session,
        action="delete",
        obj=observable,
        context_type=ctx_type,
        context_id=ctx_id,
        actor=deleted_by,
        organisation_id=observable.organisation_id,
    )
