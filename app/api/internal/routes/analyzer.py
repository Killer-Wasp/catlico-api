import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Analyzer
from app.core.configs import settings
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import connector as connector_crud
from app.crud import enrichment as enrichment_crud
from app.models.connector import Connector, ConnectorRegister, ConnectorType
from app.models.enrichment import (
    JobStatus,
    ResultSubmit,
    WorkClaim,
    WorkItem,
)
from app.models.observable import BUILTIN_OBSERVABLE_TYPES
from sqlmodel import select

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
    requested_names = [c.strip() for c in connectors.split(",") if c.strip()] if connectors else None
    # Analyzer endpoint must not claim responder jobs even when the worker
    # registers/passes responder connector names.
    stmt = select(Connector.name).where(
        Connector.connector_type == ConnectorType.analyzer.value,
    )
    if requested_names:
        stmt = stmt.where(Connector.name.in_(requested_names))
    analyzer_names = [r[0] for r in (await session.execute(stmt)).all()]
    if not analyzer_names:
        return WorkClaim(items=[])

    jobs = await enrichment_crud.claim_work(
        session,
        analyzer_names,
        limit=limit,
        default_lease_seconds=settings.ANALYZER_LEASE_SECONDS,
        max_lease_seconds=settings.ANALYZER_LEASE_SECONDS_MAX,
        lease_grace_seconds=settings.ANALYZER_LEASE_GRACE_SECONDS,
        max_attempts=settings.ANALYZER_MAX_ATTEMPTS,
    )
    config_cache: dict[str, dict] = {}
    items: list[WorkItem] = []
    for job in jobs:
        if job.connector_name not in config_cache:
            config_cache[job.connector_name] = await connector_crud.get_decrypted_config(
                session, job.connector_name
            )
        # Populate file_ref for attachment-backed observables (F2)
        file_ref: dict | None = None
        if BUILTIN_OBSERVABLE_TYPES.get(job.data_type, False):
            from app.crud.observable import get_observable

            obs = await get_observable(session, job.observable_id)
            if obs is not None:
                link_blob = await attachment_crud.first_observable_link(session, obs.id)
                if link_blob is not None:
                    link, blob = link_blob
                    file_ref = {
                        "attachment_id": str(blob.id),
                        "attachment_link_id": str(link.id),
                        "filename": link.name,
                        "sha256": blob.sha256,
                        "size": blob.size,
                        "content_type": blob.content_type,
                        "observable_id": str(obs.id),
                        "download_url": f"/api/internal/analyzer/files/{obs.id}",
                        "expires_at": job.lease_expires_at.isoformat()
                        if job.lease_expires_at
                        else None,
                    }
        items.append(
            WorkItem(
                job_id=job.id,
                lease_token=job.lease_token,
                connector_name=job.connector_name,
                connector_type=ConnectorType.analyzer.value,
                connector_version=job.connector_version,
                data_type=job.data_type,
                data=job.data,
                tlp=job.tlp,
                pap=job.pap,
                config=config_cache[job.connector_name],
                file_ref=file_ref,
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


# --- File download (F2) -----------------------------------------------------


@router.get("/files/{observable_id}")
async def download_observable_file(
    observable_id: uuid.UUID,
    _: Analyzer,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    """Stream the attachment blob for a file observable (analyzer-authenticated).
    Used by Konnect workers to fetch file content for Category C connectors."""
    from app.crud.observable import get_observable

    obs = await get_observable(session, observable_id)
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
        )
    link_blob = await attachment_crud.first_observable_link(session, obs.id)
    if link_blob is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No file attached to observable"
        )
    link, blob = link_blob

    async def _stream():
        async for chunk in storage.stream(blob.sha256):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type=blob.content_type,
        headers={
            "Content-Length": str(blob.size),
            "Content-Disposition": f'attachment; filename="{link.name}"',
            "X-SHA256": blob.sha256,
        },
    )
