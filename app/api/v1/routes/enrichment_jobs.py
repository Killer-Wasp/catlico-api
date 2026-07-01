import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import enrichment as enrichment_crud
from app.models.common import Page
from app.models.connector import Connector
from app.models.enrichment import (
    EnrichmentJob,
    EnrichmentJobDetail,
    EnrichmentJobRow,
    JobStatus,
    ReportTagPublic,
)

router = APIRouter(prefix="/enrichment-jobs", tags=["enrichment-jobs"])

TERMINAL_STATUSES = {
    JobStatus.success.value,
    JobStatus.failure.value,
    JobStatus.cancelled.value,
}


def _require(perm: str, perms: set[str]) -> None:
    if perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


async def _display_names(
    session: AsyncSession, connector_names: set[str]
) -> dict[str, str]:
    """connector_name -> display_name for the connectors referenced by a page of
    jobs. A job's connector may have been removed from the catalog; fall back to
    the snapshotted name so the row still renders."""
    if not connector_names:
        return {}
    rows = (
        await session.execute(
            select(Connector.name, Connector.display_name).where(
                Connector.name.in_(connector_names)
            )
        )
    ).all()
    return {name: display or name for name, display in rows}


def _to_row(job: EnrichmentJob, display_name: str) -> EnrichmentJobRow:
    return EnrichmentJobRow(
        id=job.id,
        observable_id=job.observable_id,
        connector_name=job.connector_name,
        connector_display_name=display_name,
        connector_version=job.connector_version,
        data_type=job.data_type,
        data=job.data,
        status=job.status,
        verdict=job.verdict,
        error=job.error,
        from_cache=job.from_cache,
        attempts=job.attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        ended_at=job.ended_at,
    )


@router.get("", response_model=Page[EnrichmentJobRow])
async def list_enrichment_jobs(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query()] = None,
    skip: int = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Page[EnrichmentJobRow]:
    """The active org's analyzer-job queue, newest first. `status` filters by
    queue tab (queued/running/success/failure/cancelled); omit it for all."""
    _require("read:observable", ctx.permissions)
    jobs, total = await enrichment_crud.list_for_org(
        session, ctx.organisation_id, status=status, skip=skip, limit=limit
    )
    names = await _display_names(session, {j.connector_name for j in jobs})
    items = [_to_row(j, names.get(j.connector_name, j.connector_name)) for j in jobs]
    return Page(items=items, total=total, skip=skip, limit=limit)


@router.get("/{job_id}", response_model=EnrichmentJobDetail)
async def get_enrichment_job(
    job_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EnrichmentJobDetail:
    _require("read:observable", ctx.permissions)
    job = await enrichment_crud.get_for_org(session, ctx.organisation_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    names = await _display_names(session, {job.connector_name})
    row = _to_row(job, names.get(job.connector_name, job.connector_name))
    tags = await enrichment_crud.list_report_tags(session, job.observable_id)
    return EnrichmentJobDetail(
        **row.model_dump(),
        tlp=job.tlp,
        pap=job.pap,
        report=job.report,
        tags=[
            ReportTagPublic.model_validate(t, from_attributes=True)
            for t in tags
            if t.connector_name == job.connector_name
        ],
        created_by=job.created_by,
    )


@router.post("/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_enrichment_job(
    job_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Drop a job that hasn't finished. Queued jobs are removed before they run;
    a leased (running) job is removed too — the worker's later result submit just
    404s on the missing job. Terminal jobs can't be cancelled (use clear)."""
    _require("run:enrichment", ctx.permissions)
    job = await enrichment_crud.get_for_org(session, ctx.organisation_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job has already finished",
        )
    await enrichment_crud.delete_job(session, job)


@router.post("/retry-failed")
async def retry_failed_jobs(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Requeue every failed job for the org so the analyzer picks them up again."""
    _require("run:enrichment", ctx.permissions)
    requeued = await enrichment_crud.retry_failed_for_org(session, ctx.organisation_id)
    return {"requeued": requeued}


@router.post("/clear-finished")
async def clear_finished_jobs(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Remove the org's terminal jobs (success/failure/cancelled) from the queue."""
    _require("run:enrichment", ctx.permissions)
    cleared = await enrichment_crud.clear_finished_for_org(session, ctx.organisation_id)
    return {"cleared": cleared}
