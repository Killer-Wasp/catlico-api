"""F4: MISP server and import/export routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext, CaseAuthContext, require_case_permission
from app.core.db import get_session
from app.crud import misp as misp_crud
from app.models.misp import (
    MispExportRequest,
    MispImportRequest,
    MispServerCreate,
    MispServerPublic,
    MispServerUpdate,
)

router = APIRouter(prefix="/misp", tags=["misp"])


def _ensure_org_admin(ctx: ActiveOrgContext) -> None:
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )


# --- Servers ---


@router.get("/servers", response_model=list[MispServerPublic])
async def list_servers(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[MispServerPublic]:
    _ensure_org_admin(ctx)
    servers = await misp_crud.list_servers(session, ctx.organisation_id)
    return [
        MispServerPublic(
            id=s.id,
            name=s.name,
            url=s.url,
            enabled=s.enabled,
            verify_ssl=s.verify_ssl,
            has_auth_key=s.auth_key_encrypted is not None,
            organisation_id=s.organisation_id,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in servers
    ]


@router.post("/servers", response_model=MispServerPublic, status_code=status.HTTP_201_CREATED)
async def create_server(
    server_in: MispServerCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MispServerPublic:
    _ensure_org_admin(ctx)
    s = await misp_crud.create_server(
        session, server_in, organisation_id=ctx.organisation_id, created_by=str(ctx.user.id)
    )
    return MispServerPublic(
        id=s.id, name=s.name, url=s.url, enabled=s.enabled,
        verify_ssl=s.verify_ssl, has_auth_key=s.auth_key_encrypted is not None,
        organisation_id=s.organisation_id, created_at=s.created_at, updated_at=s.updated_at,
    )


@router.patch("/servers/{server_id}", response_model=MispServerPublic)
async def update_server(
    server_id: uuid.UUID,
    server_in: MispServerUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MispServerPublic:
    _ensure_org_admin(ctx)
    s = await misp_crud.get_server(session, server_id, ctx.organisation_id)
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MISP server not found")
    s = await misp_crud.update_server(session, s, server_in, updated_by=str(ctx.user.id))
    return MispServerPublic(
        id=s.id, name=s.name, url=s.url, enabled=s.enabled,
        verify_ssl=s.verify_ssl, has_auth_key=s.auth_key_encrypted is not None,
        organisation_id=s.organisation_id, created_at=s.created_at, updated_at=s.updated_at,
    )


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _ensure_org_admin(ctx)
    s = await misp_crud.get_server(session, server_id, ctx.organisation_id)
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MISP server not found")
    await misp_crud.delete_server(session, s)


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _ensure_org_admin(ctx)
    s = await misp_crud.get_server(session, server_id, ctx.organisation_id)
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MISP server not found")
    # ponytail: 501 until MISP integration is implemented (P1.4)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MISP connection test not yet implemented",
    )


@router.post("/servers/{server_id}/import-now")
async def import_now(
    server_id: uuid.UUID,
    body: MispImportRequest,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _ensure_org_admin(ctx)
    s = await misp_crud.get_server(session, server_id, ctx.organisation_id)
    if not s:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MISP server not found")
    # ponytail: 501 until MISP integration is implemented (P1.4)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MISP import not yet implemented",
    )


@router.post("/cases/{case_id}/export/misp")
async def export_case(
    case_id: int,
    body: MispExportRequest,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    # ponytail: 501 until MISP integration is implemented (P1.4)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MISP export not yet implemented",
    )
