"""G2+G3: Metrics and dashboards CRUD + routes (condensed)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud.pagination import paginate

# --- Metrics ---

from app.models.metric import (
    CaseMetricUpdate, CaseMetricPublic, Metric, MetricCreate, MetricUpdate, MetricPublic, CaseMetricValue,
)
from app.models.dashboard import Dashboard, DashboardCreate, DashboardUpdate, DashboardPublic
from app.models.common import Page

router = APIRouter(prefix="/metrics", tags=["metrics"])


async def _ensure_admin(ctx: ActiveOrgContext):
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org admin required")


@router.get("", response_model=list[MetricPublic])
async def list_metrics(ctx: ActiveOrgContext, db=Depends(get_session)):
    result = await db.execute(select(Metric).where(Metric.organisation_id == ctx.organisation_id).order_by(Metric.name))
    return [MetricPublic(**m.__dict__) for m in result.scalars().all()]


@router.post("", response_model=MetricPublic, status_code=201)
async def create_metric(body: MetricCreate, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    m = Metric(organisation_id=ctx.organisation_id, name=body.name, description=body.description,
               data_type=body.data_type, config=body.config, created_by=str(ctx.user.id))
    db.add(m); await db.flush(); return MetricPublic(**m.__dict__)


@router.patch("/{metric_id}", response_model=MetricPublic)
async def update_metric(metric_id: uuid.UUID, body: MetricUpdate, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    m = await db.get(Metric, metric_id)
    if not m or m.organisation_id != ctx.organisation_id:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items(): setattr(m, k, v)
    m.updated_by = str(ctx.user.id); db.add(m); await db.flush(); return MetricPublic(**m.__dict__)


@router.delete("/{metric_id}", status_code=204)
async def delete_metric(metric_id: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    m = await db.get(Metric, metric_id)
    if not m or m.organisation_id != ctx.organisation_id: raise HTTPException(404, "Not found")
    await db.delete(m); await db.flush()


# --- Case metrics ---

@router.get("/cases/{case_id}/metrics", response_model=list[CaseMetricPublic])
async def list_case_metrics(case_id: int, ctx: ActiveOrgContext, db=Depends(get_session)):
    result = await db.execute(select(CaseMetricValue).where(CaseMetricValue.case_id == case_id))
    vals = result.scalars().all()
    out = []
    for v in vals:
        m = await db.get(Metric, v.metric_id)
        out.append(CaseMetricPublic(metric_id=v.metric_id, metric_name=m.name if m else "?", value=v.value, updated_at=v.updated_at))
    return out


@router.put("/cases/{case_id}/metrics", response_model=list[CaseMetricPublic])
async def update_case_metrics(case_id: int, body: CaseMetricUpdate, ctx: ActiveOrgContext, db=Depends(get_session)):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for mid, val in body.metrics.items():
        row = CaseMetricValue(case_id=case_id, metric_id=uuid.UUID(mid), value=val, updated_at=now)
        await db.merge(row)
    await db.flush()
    # Return updated
    result = await db.execute(select(CaseMetricValue).where(CaseMetricValue.case_id == case_id))
    vals = result.scalars().all()
    out = []
    for v in vals:
        m = await db.get(Metric, v.metric_id)
        out.append(CaseMetricPublic(metric_id=v.metric_id, metric_name=m.name if m else "?", value=v.value, updated_at=v.updated_at))
    return out


# --- Dashboards ---

dash_router = APIRouter(prefix="/dashboards", tags=["dashboards"])


@dash_router.get("", response_model=list[DashboardPublic])
async def list_dashboards(ctx: ActiveOrgContext, db=Depends(get_session)):
    result = await db.execute(select(Dashboard).where(Dashboard.organisation_id == ctx.organisation_id).order_by(Dashboard.name))
    return [DashboardPublic(**d.__dict__) for d in result.scalars().all()]


@dash_router.post("", response_model=DashboardPublic, status_code=201)
async def create_dashboard(body: DashboardCreate, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    d = Dashboard(organisation_id=ctx.organisation_id, name=body.name, description=body.description,
                  layout=body.layout, is_public=body.is_public, created_by=str(ctx.user.id))
    db.add(d); await db.flush(); return DashboardPublic(**d.__dict__)


@dash_router.patch("/{dashboard_id}", response_model=DashboardPublic)
async def update_dashboard(did: uuid.UUID, body: DashboardUpdate, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    d = await db.get(Dashboard, did)
    if not d or d.organisation_id != ctx.organisation_id: raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items(): setattr(d, k, v)
    d.updated_by = str(ctx.user.id); db.add(d); await db.flush(); return DashboardPublic(**d.__dict__)


@dash_router.delete("/{dashboard_id}", status_code=204)
async def delete_dashboard(did: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    await _ensure_admin(ctx)
    d = await db.get(Dashboard, did)
    if not d or d.organisation_id != ctx.organisation_id: raise HTTPException(404, "Not found")
    await db.delete(d); await db.flush()
