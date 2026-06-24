import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.models.role import Permission, Role, RoleCreate, RolePermission, RoleUpdate


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> Role | None:
    return await session.get(Role, role_id)


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    result = await session.execute(select(Role).where(Role.name == name))
    return result.scalar_one_or_none()


async def get_roles(session: AsyncSession, skip: int = 0, limit: int = 100) -> list[Role]:
    result = await session.execute(select(Role).offset(skip).limit(limit))
    return list(result.scalars().all())


async def get_role_permissions(session: AsyncSession, role_id: uuid.UUID) -> list[str]:
    result = await session.execute(
        select(RolePermission.permission).where(RolePermission.role_id == role_id)
    )
    return list(result.scalars().all())


async def create_role(
    session: AsyncSession, role_in: RoleCreate, created_by: str
) -> Role:
    role = Role(name=role_in.name, created_by=created_by)
    session.add(role)
    await session.flush()
    for perm in role_in.permissions:
        session.add(RolePermission(role_id=role.id, permission=perm.value))
    await session.commit()
    await session.refresh(role)
    return role


async def update_role_permissions(
    session: AsyncSession, role: Role, role_in: RoleUpdate, updated_by: str
) -> Role:
    await session.execute(
        delete(RolePermission).where(RolePermission.role_id == role.id)
    )
    for perm in role_in.permissions:
        session.add(RolePermission(role_id=role.id, permission=perm.value))
    role.updated_at = datetime.now(UTC)
    role.updated_by = updated_by
    session.add(role)
    await session.commit()
    await session.refresh(role)
    return role


async def delete_role(session: AsyncSession, role: Role) -> None:
    await session.delete(role)
    await session.commit()


async def upsert_builtin_role(
    session: AsyncSession, name: str, permissions: set[Permission], created_by: str
) -> Role:
    role = await get_role_by_name(session, name)
    if not role:
        role = Role(name=name, created_by=created_by)
        session.add(role)
        await session.flush()
        for perm in permissions:
            session.add(RolePermission(role_id=role.id, permission=perm.value))
        await session.commit()
        await session.refresh(role)
        return role

    # Add any newly defined permissions that the existing role is missing.
    existing_perms = set(await get_role_permissions(session, role.id))
    new_perms = {p.value for p in permissions} - existing_perms
    if new_perms:
        for perm in new_perms:
            session.add(RolePermission(role_id=role.id, permission=perm))
        await session.commit()
        await session.refresh(role)
    return role
