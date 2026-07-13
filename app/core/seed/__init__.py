"""JSON-driven database seeding.

Profiles live under ``app/core/seed_data/<profile>/`` as per-entity JSON files
(see :mod:`app.core.seed.schema`). Which profile is applied on local startup is
controlled by the ``SEED_PROFILE`` setting (default ``"demo"``; ``"none"`` skips
seeding).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seed.loader import load_profile, seed_from_profile

__all__ = ["load_profile", "seed_from_profile", "seed_local_demo_data"]


async def seed_local_demo_data(session: AsyncSession) -> None:
    """Seed the DB from the configured profile. Kept for the startup call site;
    a no-op when ``SEED_PROFILE`` is ``"none"``."""
    from app.core.configs import settings

    profile = settings.SEED_PROFILE
    if profile.lower() == "none":
        return
    await seed_from_profile(session, profile)
