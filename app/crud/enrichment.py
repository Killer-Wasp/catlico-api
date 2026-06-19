import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import observable as obs_crud
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


async def claim_work(
    session: AsyncSession,
    connector_names: list[str] | None,
    *,
    limit: int,
    lease_seconds: int,
    max_attempts: int,
) -> list[EnrichmentJob]:
    """Lease up to `limit` runnable jobs. Picks queued jobs and leased jobs whose
    lease has expired; uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent
    analyzer polls never grab the same row."""
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


async def list_report_tags(
    session: AsyncSession, observable_id: uuid.UUID
) -> list[ReportTag]:
    stmt = select(ReportTag).where(ReportTag.observable_id == observable_id)
    return list((await session.execute(stmt)).scalars().all())
