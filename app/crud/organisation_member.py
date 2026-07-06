import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.organisation_member import (
    OrganisationMember,
    OrganisationMemberCreate,
    OrganisationMemberUpdate,
)
from app.models.role import RolePermission
from app.models.user import User


async def get_member(
    session: AsyncSession, user_id: uuid.UUID, organisation_id: str
) -> OrganisationMember | None:
    result = await session.execute(
        select(OrganisationMember).where(
            OrganisationMember.user_id == user_id,
            OrganisationMember.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def get_members(
    session: AsyncSession, organisation_id: str
) -> list[tuple[OrganisationMember, User]]:
    """Org members paired with their User (joined), for member lists and
    @-mention pickers — carries email, name and avatar flag per row."""
    result = await session.execute(
        select(OrganisationMember, User)
        .join(User, User.id == OrganisationMember.user_id)
        .where(OrganisationMember.organisation_id == organisation_id)
        .order_by(User.email)
    )
    return [(member, user) for member, user in result.all()]


async def get_user_organisations(
    session: AsyncSession, user_id: uuid.UUID
) -> list[str]:
    result = await session.execute(
        select(OrganisationMember.organisation_id).where(
            OrganisationMember.user_id == user_id
        )
    )
    return list(result.scalars().all())


async def get_member_permissions(
    session: AsyncSession, user_id: uuid.UUID, organisation_id: str
) -> set[str]:
    result = await session.execute(
        select(RolePermission.permission)
        .join(OrganisationMember, OrganisationMember.role_id == RolePermission.role_id)
        .where(
            OrganisationMember.user_id == user_id,
            OrganisationMember.organisation_id == organisation_id,
        )
    )
    return set(result.scalars().all())


async def add_member(
    session: AsyncSession,
    organisation_id: str,
    member_in: OrganisationMemberCreate,
    created_by: str,
) -> OrganisationMember:
    member = OrganisationMember(
        user_id=member_in.user_id,
        organisation_id=organisation_id,
        role_id=member_in.role_id,
        created_by=created_by,
    )
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


async def update_member(
    session: AsyncSession,
    member: OrganisationMember,
    member_in: OrganisationMemberUpdate,
    updated_by: str,
) -> OrganisationMember:
    from datetime import UTC, datetime

    member.role_id = member_in.role_id
    member.updated_at = datetime.now(UTC)
    member.updated_by = updated_by
    session.add(member)
    await session.commit()
    await session.refresh(member)
    return member


async def remove_member(session: AsyncSession, member: OrganisationMember) -> None:
    await session.delete(member)
    await session.commit()
