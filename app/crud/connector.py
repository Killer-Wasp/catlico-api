from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.crypto import decrypt_secrets, encrypt_secrets
from app.models.connector import (
    Connector,
    ConnectorRegisterItem,
    ConnectorSecret,
    OrgConnector,
)


async def get(session: AsyncSession, name: str) -> Connector | None:
    return await session.get(Connector, name)


async def list_catalog(session: AsyncSession) -> list[Connector]:
    result = await session.execute(select(Connector).order_by(Connector.name))
    return list(result.scalars().all())


async def upsert_from_register(
    session: AsyncSession, items: list[ConnectorRegisterItem], *, created_by: str
) -> list[Connector]:
    """Register/refresh connector definitions reported by the analyzer."""
    out: list[Connector] = []
    for item in items:
        existing = await session.get(Connector, item.name)
        if existing is None:
            existing = Connector(name=item.name, created_by=created_by)
        existing.display_name = item.display_name or item.name
        existing.connector_type = item.connector_type
        existing.version = item.version
        existing.data_types = item.data_types
        existing.description = item.description
        existing.manifest = item.manifest
        existing.available = True
        existing.updated_at = datetime.now(UTC)
        existing.updated_by = created_by
        session.add(existing)
        out.append(existing)
    await session.flush()
    return out


# --- per-org enablement ---


async def get_org_connector(
    session: AsyncSession, organisation_id: str, name: str
) -> OrgConnector | None:
    return await session.get(OrgConnector, (organisation_id, name))


async def set_org_enabled(
    session: AsyncSession,
    organisation_id: str,
    name: str,
    *,
    enabled: bool,
    updated_by: str,
) -> OrgConnector:
    row = await get_org_connector(session, organisation_id, name)
    if row is None:
        row = OrgConnector(
            organisation_id=organisation_id,
            connector_name=name,
            enabled=enabled,
            created_by=updated_by,
        )
    else:
        row.enabled = enabled
        row.updated_at = datetime.now(UTC)
        row.updated_by = updated_by
    session.add(row)
    await session.flush()
    return row


async def is_enabled_for_org(
    session: AsyncSession, organisation_id: str, name: str
) -> bool:
    row = await get_org_connector(session, organisation_id, name)
    return bool(row and row.enabled)


async def list_enabled_for_org(
    session: AsyncSession, organisation_id: str
) -> list[Connector]:
    """Available connectors this org has explicitly enabled."""
    stmt = (
        select(Connector)
        .join(OrgConnector, OrgConnector.connector_name == Connector.name)
        .where(
            OrgConnector.organisation_id == organisation_id,
            OrgConnector.enabled == True,  # noqa: E712
            Connector.available == True,  # noqa: E712
        )
        .order_by(Connector.name)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# --- global settings + secrets (super-admin) ---


async def get_secret(session: AsyncSession, name: str) -> ConnectorSecret | None:
    return await session.get(ConnectorSecret, name)


async def upsert_global_config(
    session: AsyncSession,
    name: str,
    *,
    settings_in: dict,
    secrets_in: dict,
    updated_by: str,
) -> ConnectorSecret:
    row = await get_secret(session, name)
    if row is None:
        row = ConnectorSecret(connector_name=name)
    row.settings = settings_in
    # Only overwrite secrets when a non-empty dict is supplied, so a settings-only
    # edit doesn't wipe stored keys.
    if secrets_in:
        row.secrets_encrypted = encrypt_secrets(secrets_in)
    row.updated_at = datetime.now(UTC)
    row.updated_by = updated_by
    session.add(row)
    await session.flush()
    return row


async def get_decrypted_config(session: AsyncSession, name: str) -> dict:
    """Merged settings + decrypted secrets, shipped to the analyzer in the lease."""
    row = await get_secret(session, name)
    if row is None:
        return {}
    return {**(row.settings or {}), **decrypt_secrets(row.secrets_encrypted)}
