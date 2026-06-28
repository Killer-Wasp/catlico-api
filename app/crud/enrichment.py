import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import observable as obs_crud
from app.crud.connector import list_auto_run_enabled
from app.models.connector import Connector
from app.models.enrichment import (
    EnrichmentJob,
    JobStatus,
    ReportTag,
    ResultSubmit,
)
from app.models.observable import Observable


def compute_cache_key(connector_name: str, version: str, data_type: str, data: str) -> str:
    raw = f"{connector_name}|{version}|{data_type}|{data}".encode()
    return hashlib.sha256(raw).hexdigest()


async def _find_fresh_success(
    session: AsyncSession, cache_key: str, *, ttl_seconds: int
) -> EnrichmentJob | None:
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
    stmt = (
        select(EnrichmentJob)
        .where(
            EnrichmentJob.cache_key == cache_key,
            EnrichmentJob.status == JobStatus.success.value,
            EnrichmentJob.ended_at >= cutoff,
        )
        .order_by(EnrichmentJob.ended_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def enqueue(
    session: AsyncSession,
    observable: Observable,
    connector: Connector,
    *,
    organisation_id: str,
    created_by: str,
    force_refresh: bool,
    ttl_seconds: int,
) -> tuple[EnrichmentJob, bool]:
    """Return (job, created). When a fresh successful job exists and not force_refresh,
    reuse it (created=False) instead of dispatching again."""
    cache_key = compute_cache_key(
        connector.name, connector.version, observable.observable_type, observable.data
    )
    if not force_refresh:
        cached = await _find_fresh_success(session, cache_key, ttl_seconds=ttl_seconds)
        if cached is not None:
            return cached, False

    job = EnrichmentJob(
        organisation_id=organisation_id,
        observable_id=observable.id,
        connector_name=connector.name,
        connector_version=connector.version,
        data_type=observable.observable_type,
        data=observable.data,
        tlp=observable.tlp,
        pap=2,
        status=JobStatus.queued.value,
        cache_key=cache_key,
        created_by=created_by,
    )
    session.add(job)
    await session.flush()
    return job, True


def _effective_lease_seconds(
    runtime: int | None, *, default: int, grace: int, cap: int
) -> int:
    """Lease duration for a job: the connector's declared runtime plus grace so the
    worker can kill and report a timeout before the lease expires, clamped to `cap`.
    Falls back to `default` when the connector declared no runtime."""
    base = (runtime + grace) if runtime else default
    return max(1, min(base, cap))


async def claim_work(
    session: AsyncSession,
    connector_names: list[str] | None,
    *,
    limit: int,
    default_lease_seconds: int,
    max_lease_seconds: int,
    lease_grace_seconds: int,
    max_attempts: int,
) -> list[EnrichmentJob]:
    """Lease up to `limit` runnable jobs. Picks queued jobs and leased jobs whose
    lease has expired; uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent
    analyzer polls never grab the same row. Each job is leased for its connector's
    declared runtime (+grace, clamped) so slow connectors aren't re-leased mid-run."""
    now = datetime.now(UTC)
    stmt = select(EnrichmentJob).where(
        or_(
            EnrichmentJob.status == JobStatus.queued.value,
            and_(
                EnrichmentJob.status == JobStatus.leased.value,
                EnrichmentJob.lease_expires_at < now,
            ),
        )
    )
    if connector_names:
        stmt = stmt.where(EnrichmentJob.connector_name.in_(connector_names))
    stmt = stmt.order_by(EnrichmentJob.queued_at).limit(limit)
    stmt = stmt.with_for_update(skip_locked=True)

    candidates = list((await session.execute(stmt)).scalars().all())
    if not candidates:
        return []

    # Per-connector runtime, batch-loaded for the candidates we're about to lease.
    names = {job.connector_name for job in candidates}
    runtimes = dict(
        (
            await session.execute(
                select(Connector.name, Connector.max_runtime_seconds).where(
                    Connector.name.in_(names)
                )
            )
        ).all()
    )

    leased: list[EnrichmentJob] = []
    for job in candidates:
        if job.attempts >= max_attempts:
            job.status = JobStatus.failure.value
            job.error = "max lease attempts exceeded"
            job.ended_at = now
            job.lease_token = None
            job.lease_expires_at = None
            session.add(job)
            continue
        lease_seconds = _effective_lease_seconds(
            runtimes.get(job.connector_name),
            default=default_lease_seconds,
            grace=lease_grace_seconds,
            cap=max_lease_seconds,
        )
        job.status = JobStatus.leased.value
        job.lease_token = uuid.uuid4()
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempts += 1
        if job.started_at is None:
            job.started_at = now
        session.add(job)
        leased.append(job)
    await session.flush()
    return leased


async def _replace_report_tags(
    session: AsyncSession, job: EnrichmentJob, body: ResultSubmit
) -> None:
    await session.execute(
        delete(ReportTag).where(
            ReportTag.observable_id == job.observable_id,
            ReportTag.connector_name == job.connector_name,
        )
    )
    for tax in body.taxonomies:
        session.add(
            ReportTag(
                observable_id=job.observable_id,
                job_id=job.id,
                connector_name=job.connector_name,
                namespace=tax.namespace,
                predicate=tax.predicate,
                value=tax.value,
                level=tax.level,
            )
        )


async def _import_artifacts(
    session: AsyncSession, job: EnrichmentJob, body: ResultSubmit
) -> int:
    """Import returned artifacts as case observables (deduped). Alert observables
    don't import (no case to attach to). Invalid/unknown types are skipped."""
    obs = await obs_crud.get_observable(session, job.observable_id)
    if obs is None or obs.case_id is None:
        return 0
    imported = 0
    for art in body.artifacts:
        if await obs_crud.check_creatable_type(session, art.observable_type) is not None:
            continue
        if await obs_crud.find_case_observable(
            session, obs.case_id, art.observable_type, art.data
        ):
            continue
        await obs_crud.create_case_observable(
            session,
            art,
            case_id=obs.case_id,
            organisation_id=job.organisation_id,
            created_by=f"connector:{job.connector_name}",
        )
        imported += 1
    return imported


async def submit_result(
    session: AsyncSession, job: EnrichmentJob, body: ResultSubmit
) -> EnrichmentJob:
    now = datetime.now(UTC)
    job.lease_token = None
    job.lease_expires_at = None
    job.ended_at = now
    job.from_cache = False

    if body.status == JobStatus.success.value:
        job.status = JobStatus.success.value
        job.verdict = body.verdict
        job.report = body.full
        job.error = None
        await _replace_report_tags(session, job, body)
        await _import_artifacts(session, job, body)
    else:
        job.status = JobStatus.failure.value
        job.error = body.error or "analyzer reported failure"

    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> EnrichmentJob | None:
    return await session.get(EnrichmentJob, job_id)


async def list_for_observable(
    session: AsyncSession, observable_id: uuid.UUID
) -> list[EnrichmentJob]:
    stmt = (
        select(EnrichmentJob)
        .where(EnrichmentJob.observable_id == observable_id)
        .order_by(EnrichmentJob.queued_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


# `running` is the queue's name for a leased job; the rest map 1:1 to JobStatus.
_STATUS_FILTERS = {
    "queued": [JobStatus.queued.value],
    "running": [JobStatus.leased.value],
    "success": [JobStatus.success.value],
    "failure": [JobStatus.failure.value],
    "cancelled": [JobStatus.cancelled.value],
}


def status_filter_values(status: str | None) -> list[str] | None:
    """Translate a queue-tab name to the underlying job statuses, or None for
    'all'. Unknown names also collapse to None (no filter)."""
    if not status or status == "all":
        return None
    return _STATUS_FILTERS.get(status)


async def list_for_org(
    session: AsyncSession,
    organisation_id: str,
    *,
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[EnrichmentJob], int]:
    """The org's enrichment jobs, newest first, optionally filtered by queue tab.
    Tenant isolation rides EnrichmentJob.organisation_id."""
    where = [EnrichmentJob.organisation_id == organisation_id]
    statuses = status_filter_values(status)
    if statuses is not None:
        where.append(EnrichmentJob.status.in_(statuses))

    total = (
        await session.execute(
            select(func.count()).select_from(EnrichmentJob).where(*where)
        )
    ).scalar_one()
    stmt = (
        select(EnrichmentJob)
        .where(*where)
        .order_by(EnrichmentJob.queued_at.desc())
        .offset(skip)
        .limit(limit)
    )
    jobs = list((await session.execute(stmt)).scalars().all())
    return jobs, total


async def get_for_org(
    session: AsyncSession, organisation_id: str, job_id: uuid.UUID
) -> EnrichmentJob | None:
    job = await session.get(EnrichmentJob, job_id)
    if job is None or job.organisation_id != organisation_id:
        return None
    return job


async def delete_job(session: AsyncSession, job: EnrichmentJob) -> None:
    await session.delete(job)
    await session.flush()


def _requeue(job: EnrichmentJob, now: datetime) -> None:
    job.status = JobStatus.queued.value
    job.verdict = None
    job.error = None
    job.report = None
    job.from_cache = False
    job.attempts = 0
    job.lease_token = None
    job.lease_expires_at = None
    job.started_at = None
    job.ended_at = None
    job.queued_at = now


async def retry_failed_for_org(session: AsyncSession, organisation_id: str) -> int:
    """Requeue every failed job for the org (fresh attempt counter, cleared
    verdict/report). Returns how many were requeued."""
    stmt = select(EnrichmentJob).where(
        EnrichmentJob.organisation_id == organisation_id,
        EnrichmentJob.status == JobStatus.failure.value,
    )
    jobs = list((await session.execute(stmt)).scalars().all())
    now = datetime.now(UTC)
    for job in jobs:
        _requeue(job, now)
        session.add(job)
    await session.flush()
    return len(jobs)


async def clear_finished_for_org(session: AsyncSession, organisation_id: str) -> int:
    """Delete the org's terminal jobs (success/failure/cancelled). Returns the
    number removed."""
    terminal = [
        JobStatus.success.value,
        JobStatus.failure.value,
        JobStatus.cancelled.value,
    ]
    result = await session.execute(
        delete(EnrichmentJob).where(
            EnrichmentJob.organisation_id == organisation_id,
            EnrichmentJob.status.in_(terminal),
        )
    )
    await session.flush()
    return result.rowcount or 0


async def list_report_tags(
    session: AsyncSession, observable_id: uuid.UUID
) -> list[ReportTag]:
    stmt = select(ReportTag).where(ReportTag.observable_id == observable_id)
    return list((await session.execute(stmt)).scalars().all())


# --- Auto-Enrichment (B2) ---


def _tlp_name(value: int) -> str:
    return {0: "WHITE", 1: "GREEN", 2: "AMBER", 3: "RED"}.get(value, "UNKNOWN")


def _pap_name(value: int) -> str:
    return {0: "WHITE", 1: "GREEN", 2: "AMBER", 3: "RED"}.get(value, "UNKNOWN")


async def enqueue_auto_for_observable(
    session: AsyncSession,
    observable: Observable,
    *,
    organisation_id: str,
    created_by: str,
) -> int:
    """Auto-enqueue enrichment jobs for a newly-created observable.

    Loads all auto-run-enabled connectors for the org, filters by observable
    data type, and respects TLP/PAP guardrails from each connector's manifest.
    Returns the number of jobs enqueued."""
    auto_connectors = await list_auto_run_enabled(session, organisation_id)
    if not auto_connectors:
        return 0

    obs_type = observable.observable_type
    obs_tlp = observable.tlp
    obs_pap = observable.pap

    count = 0
    for connector in auto_connectors:
        # Filter by data type
        if obs_type not in connector.data_types:
            continue

        # Guardrails from manifest
        manifest = connector.manifest or {}
        check_tlp = manifest.get("check_tlp", False)
        max_tlp_str = manifest.get("max_tlp", "")
        check_pap = manifest.get("check_pap", False)
        max_pap_str = manifest.get("max_pap", "")

        # TLP guard: connector declares max allowed TLP
        if check_tlp and max_tlp_str:
            try:
                max_tlp_idx = {"WHITE": 0, "GREEN": 1, "AMBER": 2, "RED": 3}[max_tlp_str.upper()]
            except KeyError:
                max_tlp_idx = 3
            if obs_tlp > max_tlp_idx:
                continue

        # PAP guard: connector declares max allowed PAP
        if check_pap and max_pap_str:
            try:
                max_pap_idx = {"WHITE": 0, "GREEN": 1, "AMBER": 2, "RED": 3}[max_pap_str.upper()]
            except KeyError:
                max_pap_idx = 3
            if obs_pap > max_pap_idx:
                continue

        job, created = await enqueue(
            session,
            observable,
            connector,
            organisation_id=organisation_id,
            created_by=created_by,
            force_refresh=False,
            ttl_seconds=3600,
        )
        if created:
            count += 1

    return count
