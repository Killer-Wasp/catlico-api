from sqlalchemy.ext.asyncio import AsyncSession

from app.core.local_demo_seed import seed_local_demo_data as _seed_local_demo_data


async def seed_local_demo_data(session: AsyncSession) -> None:
    await _seed_local_demo_data(session)
