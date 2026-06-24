from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.organisation_link import (
    OrganisationLink,
    OrganisationLinkCreate,
    OrganisationLinkUpdate,
)


async def get_link(
    session: AsyncSession, from_org_id: str, to_org_id: str
) -> OrganisationLink | None:
    return await session.get(OrganisationLink, (from_org_id, to_org_id))


async def list_links(
    session: AsyncSession, org_id: str, *, outgoing: bool = True
) -> list[OrganisationLink]:
    """List links for an org. `outgoing=True` lists links *from* this org; `False`
    lists links *to* this org."""
    col = OrganisationLink.from_org_id if outgoing else OrganisationLink.to_org_id
    result = await session.execute(select(OrganisationLink).where(col == org_id))
    return list(result.scalars().all())


async def create_link(
    session: AsyncSession,
    from_org_id: str,
    link_in: OrganisationLinkCreate,
    created_by: str,
) -> OrganisationLink:
    link = OrganisationLink(
        from_org_id=from_org_id,
        to_org_id=link_in.to_org_id,
        case_sharing=link_in.case_sharing,
        task_sharing=link_in.task_sharing,
        observable_sharing=link_in.observable_sharing,
        created_by=created_by,
    )
    session.add(link)
    await session.flush()
    return link


async def update_link(
    session: AsyncSession,
    link: OrganisationLink,
    link_in: OrganisationLinkUpdate,
    updated_by: str,
) -> OrganisationLink:
    update_data = link_in.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(UTC)
    update_data["updated_by"] = updated_by
    link.sqlmodel_update(update_data)
    session.add(link)
    await session.flush()
    return link


async def delete_link(
    session: AsyncSession, link: OrganisationLink
) -> None:
    await session.delete(link)
    await session.flush()
