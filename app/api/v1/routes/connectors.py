from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext, SuperAdminUser
from app.core.db import get_session
from app.crud import connector as connector_crud
from app.models.connector import (
    Connector,
    ConnectorConfigUpdate,
    ConnectorPublic,
    ConnectorSecret,
)

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _require(perm: str, perms: set[str]) -> None:
    if perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


def _to_public(
    connector: Connector, *, enabled: bool, secret: ConnectorSecret | None
) -> ConnectorPublic:
    return ConnectorPublic(
        name=connector.name,
        display_name=connector.display_name,
        connector_type=connector.connector_type,
        version=connector.version,
        data_types=connector.data_types,
        description=connector.description,
        available=connector.available,
        enabled=enabled,
        settings=(secret.settings if secret else {}) or {},
        has_secrets=bool(secret and secret.secrets_encrypted),
    )


@router.get("", response_model=list[ConnectorPublic])
async def list_connectors(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConnectorPublic]:
    _require("read:connector", ctx.permissions)
    out: list[ConnectorPublic] = []
    for c in await connector_crud.list_catalog(session):
        enabled = await connector_crud.is_enabled_for_org(
            session, ctx.organisation_id, c.name
        )
        secret = await connector_crud.get_secret(session, c.name)
        out.append(_to_public(c, enabled=enabled, secret=secret))
    return out


@router.get("/{name}", response_model=ConnectorPublic)
async def get_connector(
    name: str,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectorPublic:
    _require("read:connector", ctx.permissions)
    c = await connector_crud.get(session, name)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    enabled = await connector_crud.is_enabled_for_org(session, ctx.organisation_id, name)
    secret = await connector_crud.get_secret(session, name)
    return _to_public(c, enabled=enabled, secret=secret)


@router.put("/{name}/config", response_model=ConnectorPublic)
async def update_connector_config(
    name: str,
    body: ConnectorConfigUpdate,
    admin: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectorPublic:
    """Global settings + secrets, super-admin only. Secrets are write-only and
    redacted from every response."""
    c = await connector_crud.get(session, name)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    secret = await connector_crud.upsert_global_config(
        session,
        name,
        settings_in=body.settings,
        secrets_in=body.secrets,
        updated_by=str(admin.id),
    )
    return _to_public(c, enabled=False, secret=secret)


@router.post("/{name}/enable", response_model=ConnectorPublic)
async def enable_connector(
    name: str,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectorPublic:
    return await _set_enabled(name, ctx, session, enabled=True)


@router.post("/{name}/disable", response_model=ConnectorPublic)
async def disable_connector(
    name: str,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConnectorPublic:
    return await _set_enabled(name, ctx, session, enabled=False)


async def _set_enabled(
    name: str, ctx: ActiveOrgContext, session: AsyncSession, *, enabled: bool
) -> ConnectorPublic:
    _require("write:connector", ctx.permissions)
    c = await connector_crud.get(session, name)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await connector_crud.set_org_enabled(
        session, ctx.organisation_id, name, enabled=enabled, updated_by=str(ctx.user.id)
    )
    secret = await connector_crud.get_secret(session, name)
    return _to_public(c, enabled=enabled, secret=secret)
