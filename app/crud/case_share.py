from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.case_share import CaseShare


async def get_share(
    session: AsyncSession, case_id: int, organisation_id: str
) -> CaseShare | None:
    result = await session.execute(
        select(CaseShare).where(
            CaseShare.case_id == case_id,
            CaseShare.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def list_shares(session: AsyncSession, case_id: int) -> list[CaseShare]:
    result = await session.execute(
        select(CaseShare).where(CaseShare.case_id == case_id)
    )
    return list(result.scalars().all())


async def list_non_owner_org_ids(session: AsyncSession, case_id: int) -> list[str]:
    result = await session.execute(
        select(CaseShare.organisation_id).where(
            CaseShare.case_id == case_id,
            CaseShare.is_owner == False,  # noqa: E712
        )
    )
    return list(result.scalars().all())
