from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organisation_link import OrganisationLink


async def get_link(
    session: AsyncSession, from_org_id: str, to_org_id: str
) -> OrganisationLink | None:
    return await session.get(OrganisationLink, (from_org_id, to_org_id))
