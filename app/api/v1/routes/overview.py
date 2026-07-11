"""SOC Overview dashboard — one read-only aggregate for the landing page.

`GET /overview` returns KPI tiles, the open-alert triage queue, severity /
pipeline / workload breakdowns, the 24h ingestion series and the latest
observables, all scoped to the active organisation. See `app.crud.overview`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.api.v1.routes.case_common import require_perm
from app.core.db import get_session
from app.crud import overview as overview_crud
from app.models.overview import OverviewPublic

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("", response_model=OverviewPublic)
async def get_overview(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    trend_days: Annotated[int, Query(ge=7, le=90)] = 14,
) -> OverviewPublic:
    """Aggregate dashboard for the active org. Requires `read:case` — the same
    baseline read grant the alert/case list views use. `trend_days` widens or
    narrows the case-trend window (7–90 days)."""
    require_perm(ctx, "read:case")
    return await overview_crud.build_overview(
        session, ctx.organisation_id, trend_days=trend_days
    )
