from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.models.alert import Alert, AlertCreate, AlertStatus, AlertUpdate


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
) -> tuple[list[Alert], int]:
    base = select(Alert).where(
        Alert.organisation_id == organisation_id, Alert.deleted_at.is_(None)
    )
    if status_filter is not None:
        base = base.where(Alert.status == status_filter)
    if type_filter is not None:
        base = base.where(Alert.type == type_filter)
    if source_filter is not None:
        base = base.where(Alert.source == source_filter)
    if severity is not None:
        base = base.where(Alert.severity == severity)

    return await paginate(session, base, Alert.id.desc(), skip=skip, limit=limit)


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
