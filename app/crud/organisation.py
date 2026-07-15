from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.organisation import Organisation, OrganisationCreate, OrganisationUpdate


async def get_organisation(session: AsyncSession, organisation_id: str) -> Organisation | None:
    return await session.get(Organisation, organisation_id)


async def get_organisations(
    session: AsyncSession, skip: int = 0, limit: int = 100
) -> list[Organisation]:
    result = await session.execute(select(Organisation).offset(skip).limit(limit))
    return list(result.scalars().all())


async def create_organisation(
    session: AsyncSession, org_in: OrganisationCreate, created_by: str
) -> Organisation:
    from app.crud.case_status import seed_org_builtin_statuses
    from app.crud.role import seed_org_builtin_roles

    org = Organisation(
        id=org_in.id,
        name=org_in.name,
        description=org_in.description,
        created_by=created_by,
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    # Every org owns its own copy of the built-in roles (org-admin/analyst/read-only).
    await seed_org_builtin_roles(session, org.id, created_by)
    # ...and its own copy of the built-in case statuses (Open/In progress/etc.).
    await seed_org_builtin_statuses(session, org.id, created_by)
    await session.commit()
    return org


async def update_organisation(
    session: AsyncSession, org: Organisation, org_in: OrganisationUpdate, updated_by: str
) -> Organisation:
    update_data = org_in.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    org.sqlmodel_update(update_data)
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


async def delete_organisation(session: AsyncSession, org: Organisation) -> None:
    await session.delete(org)
    await session.commit()
