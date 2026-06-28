"""E1: Internal responder endpoints for connector worker interaction."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Analyzer
from app.core.db import get_session
from app.models.connector import ConnectorType
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
) -> list[dict]:
    """Claim pending responder jobs. Returns empty list for now — responder
    jobs are created by the konnect worker when it claims work (E1 v1)."""
    # ponytail: v1 returns empty; real impl queries enrichment_job for responder type
    return []


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
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"message": "Operation application failed", "errors": errors},
            )

    return {"ok": True, "job_id": str(job_id), "status": body.status}
