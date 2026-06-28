"""F4: MISP server CRUD."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.crypto import encrypt_secrets, decrypt_secrets
from app.models.misp import MispServer, MispServerCreate, MispServerUpdate


async def get_server(
    session: AsyncSession, server_id: uuid.UUID, organisation_id: str
) -> MispServer | None:
    result = await session.execute(
        select(MispServer).where(
            MispServer.id == server_id,
            MispServer.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def list_servers(
    session: AsyncSession, organisation_id: str
) -> list[MispServer]:
    result = await session.execute(
        select(MispServer)
        .where(MispServer.organisation_id == organisation_id)
        .order_by(MispServer.name)
    )
    return list(result.scalars().all())


async def create_server(
    session: AsyncSession,
    server_in: MispServerCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> MispServer:
    server = MispServer(
        organisation_id=organisation_id,
        name=server_in.name,
        url=server_in.url,
        auth_key_encrypted=encrypt_secrets({"key": server_in.auth_key}) if server_in.auth_key else None,
        enabled=server_in.enabled,
        verify_ssl=server_in.verify_ssl,
        config=server_in.config,
        created_by=created_by,
    )
    session.add(server)
    await session.flush()
    return server


async def update_server(
    session: AsyncSession,
    server: MispServer,
    server_in: MispServerUpdate,
    updated_by: str,
) -> MispServer:
    update_data = server_in.model_dump(exclude_unset=True)
    if "auth_key" in update_data:
        key = update_data.pop("auth_key")
        if key:
            update_data["auth_key_encrypted"] = encrypt_secrets({"key": key})
    for k, v in update_data.items():
        setattr(server, k, v)
    server.updated_by = updated_by
    session.add(server)
    await session.flush()
    return server


async def delete_server(session: AsyncSession, server: MispServer) -> None:
    await session.delete(server)
    await session.flush()
