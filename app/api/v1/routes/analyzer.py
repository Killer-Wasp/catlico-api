import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Analyzer
from app.core.configs import settings
from app.core.db import get_session
from app.crud import connector as connector_crud
from app.crud import enrichment as enrichment_crud
from app.models.connector import ConnectorRegister
from app.models.enrichment import (
    JobStatus,
    ResultSubmit,
    WorkClaim,
    WorkItem,
)

router = APIRouter(prefix="/analyzer", tags=["analyzer"])


@router.post("/register")
async def register_connectors(
    body: ConnectorRegister,
    _: Analyzer,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    rows = await connector_crud.upsert_from_register(
        session, body.connectors, created_by="analyzer"
    )
    return {"registered": [c.name for c in rows]}


@router.post("/work", response_model=WorkClaim)
async def claim_work(
    _: Analyzer,
    session: Annotated[AsyncSession, Depends(get_session)],
    connectors: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> WorkClaim:
    names = [c.strip() for c in connectors.split(",") if c.strip()] if connectors else None
    jobs = await enrichment_crud.claim_work(
        session,
        names,
        limit=limit,
        lease_seconds=settings.ANALYZER_LEASE_SECONDS,
        max_attempts=settings.ANALYZER_MAX_ATTEMPTS,
    )
    config_cache: dict[str, dict] = {}
    items: list[WorkItem] = []
    for job in jobs:
        if job.connector_name not in config_cache:
            config_cache[job.connector_name] = await connector_crud.get_decrypted_config(
                session, job.connector_name
            )
        items.append(
            WorkItem(
                job_id=job.id,
                lease_token=job.lease_token,
                connector_name=job.connector_name,
                connector_version=job.connector_version,
                data_type=job.data_type,
                data=job.data,
                tlp=job.tlp,
                pap=job.pap,
                config=config_cache[job.connector_name],
            )
        )
    return WorkClaim(items=items)


@router.post("/jobs/{job_id}/result")
async def submit_result(
    job_id: uuid.UUID,
    body: ResultSubmit,
    _: Analyzer,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    job = await enrichment_crud.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if body.status not in (JobStatus.success.value, JobStatus.failure.value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be 'success' or 'failure'",
        )
    # The lease must still be valid and held by this submitter. lease_expires_at comes
    # back tz-naive from the DB (DateTime columns carry no tz), so treat it as UTC.
    lease_exp = job.lease_expires_at
    if lease_exp is not None and lease_exp.tzinfo is None:
        lease_exp = lease_exp.replace(tzinfo=UTC)
    expired = lease_exp is not None and lease_exp < datetime.now(UTC)
    if (
        job.status != JobStatus.leased.value
        or job.lease_token is None
        or job.lease_token != body.lease_token
        or expired
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lease is invalid or expired",
        )
    job = await enrichment_crud.submit_result(session, job, body)
    return {"id": str(job.id), "status": job.status}
