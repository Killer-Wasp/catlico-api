import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.crud.case_share import list_non_owner_org_ids
from app.crud.organisation_link import get_link
from app.models.case_share import CaseShare
from app.models.observable import (
    Observable,
    ObservableCreate,
    ObservableShare,
    ObservableType,
    ObservableUpdate,
)
from app.models.organisation_link import AutoShareMode


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


async def get_type(session: AsyncSession, name: str) -> ObservableType | None:
    return await session.get(ObservableType, name)


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
    )
    session.add(observable)
    await session.flush()
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
    alert_obs, _ = await list_observables_for_alert(session, alert_id, limit=10_000)
    imported = 0
    for src in alert_obs:
        if await find_case_observable(session, case_id, src.observable_type, src.data):
            continue
        await create_case_observable(
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
    )
