"""E1: Internal responder endpoints for connector worker interaction."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Analyzer
from app.core.configs import settings
from app.core.db import get_session
from app.models.enrichment import JobStatus
from app.services.connector_operations import ResponderResult

router = APIRouter(prefix="/responder", tags=["responder"])


@router.post("/register")
async def responder_register() -> dict:
    """Stub: responder registration. The konnect worker registers responders
    the same way as analyzers. The backend accepts any connector_type now (E1)."""
    return {"ok": True, "message": "Responder registration endpoint active"}


@router.post("/work")
async def responder_claim_work(
    session: Annotated[AsyncSession, Depends(get_session)],
    _auth: Analyzer,
    connectors: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> list[dict]:
    """Claim pending responder jobs. Queries enrichment_jobs for responder-type
    connectors using the same lease pattern as analyzer work (E1)."""
    from app.crud import connector as connector_crud
    from app.crud import enrichment as enrichment_crud
    from sqlmodel import select
    from app.models.connector import Connector, ConnectorType

    requested_names = (
        [c.strip() for c in connectors.split(",") if c.strip()]
        if connectors
        else None
    )
    stmt = select(Connector.name).where(
        Connector.connector_type == ConnectorType.responder.value,
    )
    if requested_names:
        stmt = stmt.where(Connector.name.in_(requested_names))
    responder_names = [r[0] for r in (await session.execute(stmt)).all()]
    if not responder_names:
        return []

    # Claim jobs for responder connectors
    jobs = await enrichment_crud.claim_work(
        session,
        responder_names,
        limit=limit,
        default_lease_seconds=settings.ANALYZER_LEASE_SECONDS,
        max_lease_seconds=settings.ANALYZER_LEASE_SECONDS_MAX,
        lease_grace_seconds=settings.ANALYZER_LEASE_GRACE_SECONDS,
        max_attempts=settings.ANALYZER_MAX_ATTEMPTS,
    )
    config_cache: dict[str, dict] = {}
    items = []
    for job in jobs:
        if job.connector_name not in config_cache:
            config_cache[job.connector_name] = await connector_crud.get_decrypted_config(
                session, job.connector_name
            )
        items.append(
            {
                "job_id": str(job.id),
                "lease_token": str(job.lease_token) if job.lease_token else None,
                "connector_name": job.connector_name,
                "connector_type": ConnectorType.responder.value,
                "connector_version": job.connector_version,
                "data_type": job.data_type,
                "data": job.data,
                "tlp": job.tlp,
                "pap": job.pap,
                "config": config_cache[job.connector_name],
            }
        )
    return items


@router.post("/jobs/{job_id}/result")
async def responder_submit_result(
    job_id: uuid.UUID,
    body: ResponderResult,
    session: Annotated[AsyncSession, Depends(get_session)],
    _auth: Analyzer,
) -> dict:
    """Submit a responder job result with operations. Currently stores the
    result and applies operations to the case context."""
    from app.crud.enrichment import get_job

    job = await get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if body.status not in (JobStatus.success.value, JobStatus.failure.value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be 'success' or 'failure'",
        )

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

    def _finish(status_value: str, *, error: str | None = None) -> None:
        job.status = status_value
        job.error = error
        job.report = {
            "message": body.message,
            "operations": [op.model_dump() for op in body.operations],
            "full": body.full,
            "dry_run": body.dry_run,
        }
        job.lease_token = None
        job.lease_expires_at = None
        job.ended_at = datetime.now(UTC)
        session.add(job)

    if body.require_confirmation and not body.dry_run:
        _finish(JobStatus.failure.value, error="responder operation requires confirmation")
        return {
            "ok": False,
            "job_id": str(job_id),
            "status": "confirmation_required",
        }

    # Apply operations if successful
    if body.status == "success" and body.operations:
        from app.services.connector_operations import apply_operations

        # Determine case from the job context
        case_id = None
        from app.crud import observable as obs_crud
        obs = await obs_crud.get_observable(session, job.observable_id)
        if obs and obs.case_id:
            case_id = obs.case_id

        if body.dry_run:
            # Dry-run: validate, return intended changes, don't apply
            errors = await apply_operations(
                session,
                body.operations,
                organisation_id=job.organisation_id,
                connector_name=job.connector_name,
                case_id=case_id,
                dry_run=True,
            )
            _finish(JobStatus.success.value)
            return {
                "ok": True,
                "job_id": str(job_id),
                "status": "dry_run",
                "operations": [op.model_dump() for op in body.operations],
                "errors": errors if errors else None,
            }

        errors = await apply_operations(
            session,
            body.operations,
            organisation_id=job.organisation_id,
            connector_name=job.connector_name,
            case_id=case_id,
            dry_run=False,
        )
        if errors:
            _finish(JobStatus.failure.value, error="; ".join(errors))
            return {
                "ok": False,
                "job_id": str(job_id),
                "status": JobStatus.failure.value,
                "errors": errors,
            }

    _finish(body.status, error=body.error if body.status == JobStatus.failure.value else None)
    return {"ok": True, "job_id": str(job_id), "status": body.status}
