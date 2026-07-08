from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import String, cast, false, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud._filters import (
    TAG_PREFIX,
    FilterClause,
    group_by_key,
    group_tag_facets,
    parse_clauses,
    tag_key_condition,
)
from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.models.alert import Alert, AlertCreate, AlertFacets, AlertStatus, AlertUpdate
from app.models.tag import Tag, Tagging, TaggableType

_ALERT_SORT_COLUMNS = {
    "id": Alert.id,
    "age": Alert.date,
}


@dataclass(frozen=True)
class AlertListFilter:
    """Clause filter + sort for the alert list (OR-within / AND-across).
    Keys: severity, source, tlp, alert, title, and `tag:<group-key>`."""

    clauses: tuple[FilterClause, ...] = ()
    sort: str = "id"
    order: str = "desc"

    @classmethod
    def from_query(
        cls, raw_filters: list[str] | None = None, *, sort="id", order="desc"
    ) -> "AlertListFilter":
        return cls(clauses=parse_clauses(raw_filters), sort=sort, order=order)


def _alert_clause_cond(key: str, clause: FilterClause):
    """Condition for one clause, None for an unknown key (ignored). A known key
    with an unusable value yields false() — matches nothing, never widens."""
    v = clause.value
    contains = clause.contains
    if key == "severity":
        try:
            return Alert.severity == int(v)
        except ValueError:
            return false()
    if key == "tlp":
        try:
            return Alert.tlp == int(v)
        except ValueError:
            return false()
    if key == "source":
        return Alert.source.ilike(f"%{v}%") if contains else Alert.source == v
    if key == "title":
        return Alert.title.ilike(f"%{v}%") if contains else Alert.title == v
    if key == "alert":
        id_str = cast(Alert.id, String)
        vv = v.lstrip("#").removeprefix("AL-")
        return id_str.ilike(f"%{vv}%") if contains else id_str == vv
    return None


def _apply_alert_filters(stmt, f: AlertListFilter):
    for key, clauses in group_by_key(f.clauses).items():
        if key.startswith(TAG_PREFIX):
            stmt = stmt.where(
                tag_key_condition(
                    TaggableType.alert, Alert.id, key[len(TAG_PREFIX) :], clauses
                )
            )
            continue
        conds = [
            c for c in (_alert_clause_cond(key, cl) for cl in clauses) if c is not None
        ]
        if conds:
            stmt = stmt.where(or_(*conds))
    return stmt


def _alert_order_by(f: AlertListFilter):
    col = _ALERT_SORT_COLUMNS.get(f.sort, Alert.id)
    direction = col.asc() if f.order == "asc" else col.desc()
    return direction, Alert.id.desc()


async def get_alert(session: AsyncSession, alert_id: int) -> Alert | None:
    """Returns the alert only if it exists and is not soft-deleted."""
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.deleted_at is not None:
        return None
    return alert


async def get_by_dedup_key(
    session: AsyncSession,
    *,
    type: str,
    source: str,
    source_ref: str,
    organisation_id: str,
) -> Alert | None:
    """Live alert matching the per-org dedup key, if any."""
    result = await session.execute(
        select(Alert).where(
            Alert.type == type,
            Alert.source == source,
            Alert.source_ref == source_ref,
            Alert.organisation_id == organisation_id,
            Alert.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_alerts_for_org(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
    status_filter: str | None = None,
    type_filter: str | None = None,
    source_filter: str | None = None,
    severity: int | None = None,
    filters: AlertListFilter | None = None,
) -> tuple[list[Alert], int]:
    filters = filters or AlertListFilter()
    base = select(Alert).where(
        Alert.organisation_id == organisation_id, Alert.deleted_at.is_(None)
    )
    # Legacy scalar params (kept for API consumers) AND-in alongside clauses.
    if status_filter is not None:
        base = base.where(Alert.status == status_filter)
    if type_filter is not None:
        base = base.where(Alert.type == type_filter)
    if source_filter is not None:
        base = base.where(Alert.source == source_filter)
    if severity is not None:
        base = base.where(Alert.severity == severity)

    base = _apply_alert_filters(base, filters)
    return await paginate(
        session, base, *_alert_order_by(filters), skip=skip, limit=limit
    )


async def list_alerts_for_case(
    session: AsyncSession,
    case_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Alert], int]:
    """Alerts promoted into the given case (newest first), for the case's
    'Linked alerts' panel. Scoped by case_id — org ownership is already
    established by the case-permission dependency at the route."""
    base = select(Alert).where(
        Alert.case_id == case_id, Alert.deleted_at.is_(None)
    )
    return await paginate(session, base, Alert.id.desc(), skip=skip, limit=limit)


async def alert_facets(session: AsyncSession, organisation_id: str) -> AlertFacets:
    """Distinct source + tag-key values across the org's alerts, for the list
    view's filter dropdowns."""
    org_alerts = (
        select(Alert.id)
        .where(Alert.organisation_id == organisation_id, Alert.deleted_at.is_(None))
        .subquery()
    )
    sources = list(
        (
            await session.execute(
                select(Alert.source)
                .where(
                    Alert.organisation_id == organisation_id,
                    Alert.deleted_at.is_(None),
                )
                .distinct()
                .order_by(Alert.source)
            )
        ).scalars()
    )
    tag_rows = (
        (
            await session.execute(
                select(Tag)
                .join(Tagging, Tagging.tag_id == Tag.id)
                .join(org_alerts, cast(org_alerts.c.id, String) == Tagging.taggable_id)
                .where(Tagging.taggable_type == TaggableType.alert)
                .distinct()
                .order_by(Tag.namespace, Tag.predicate, Tag.value)
            )
        )
        .scalars()
        .all()
    )
    return AlertFacets(sources=sources, tag_keys=group_tag_facets(tag_rows))


async def ingest_alert(
    session: AsyncSession,
    alert_in: AlertCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> tuple[Alert, bool]:
    """Upsert on the dedup key. Returns (alert, created).

    - No live match -> create (status New).
    - Live match with follow=True -> update mutable fields + bump last_sync_date.
      Status is NOT resurrected: an Ignored/Imported alert stays put.
    - Live match with follow=False -> no-op (source told us to stop syncing).
    """
    existing = await get_by_dedup_key(
        session,
        type=alert_in.type,
        source=alert_in.source,
        source_ref=alert_in.source_ref,
        organisation_id=organisation_id,
    )
    if existing is None:
        alert = Alert(
            type=alert_in.type,
            source=alert_in.source,
            source_ref=alert_in.source_ref,
            external_link=alert_in.external_link,
            title=alert_in.title,
            description=alert_in.description,
            severity=alert_in.severity,
            tlp=alert_in.tlp,
            pap=alert_in.pap,
            follow=alert_in.follow,
            organisation_id=organisation_id,
            created_by=created_by,
        )
        if alert_in.date is not None:
            alert.date = alert_in.date
            alert.last_sync_date = alert_in.date
        session.add(alert)
        await session.flush()
        await record_audit(
            session,
            action="create",
            obj=alert,
            actor=created_by,
            details={"type": alert.type, "source": alert.source, "title": alert.title},
        )
        return alert, True

    if not existing.follow:
        return existing, False

    existing.title = alert_in.title
    existing.description = alert_in.description
    existing.severity = alert_in.severity
    existing.tlp = alert_in.tlp
    existing.pap = alert_in.pap
    if alert_in.external_link is not None:
        existing.external_link = alert_in.external_link
    existing.last_sync_date = datetime.now(UTC).replace(tzinfo=None)
    existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
    existing.updated_by = created_by
    session.add(existing)
    await session.flush()
    await record_audit(
        session,
        action="update",
        obj=existing,
        actor=created_by,
        details={"reason": "source sync"},
    )
    return existing, False


async def update_alert(
    session: AsyncSession, alert: Alert, alert_in: AlertUpdate, updated_by: str
) -> Alert:
    update_data = alert_in.model_dump(exclude_unset=True)
    changes = {
        field: [getattr(alert, field, None), new]
        for field, new in update_data.items()
        if getattr(alert, field, None) != new
    }
    update_data["updated_at"] = datetime.now(UTC).replace(tzinfo=None)
    update_data["updated_by"] = updated_by
    alert.sqlmodel_update(update_data)
    session.add(alert)
    await session.flush()
    if changes:
        await record_audit(
            session, action="update", obj=alert, actor=updated_by, details=changes
        )
    return alert


async def mark_promoted(
    session: AsyncSession, alert: Alert, *, case_id: int, updated_by: str
) -> Alert:
    alert.case_id = case_id
    alert.status = AlertStatus.imported
    alert.updated_at = datetime.now(UTC).replace(tzinfo=None)
    alert.updated_by = updated_by
    session.add(alert)
    await session.flush()
    # Scope to the new case so the promotion shows up in the case activity feed.
    await record_audit(
        session,
        action="update",
        obj=alert,
        context_type="case",
        context_id=str(case_id),
        actor=updated_by,
        details={"promoted_to_case": case_id, "status": AlertStatus.imported.value},
    )
    return alert


async def delete_alert(session: AsyncSession, alert: Alert, deleted_by: str) -> None:
    """Soft delete."""
    alert.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    alert.deleted_by = deleted_by
    session.add(alert)
    await session.flush()
    await record_audit(session, action="delete", obj=alert, actor=deleted_by)
