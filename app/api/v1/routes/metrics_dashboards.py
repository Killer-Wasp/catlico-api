"""G2+G3: Metrics and dashboards CRUD + routes (condensed)."""

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgContext, CaseAuthContext, require_case_permission
from app.core.db import get_session
from app.crud.pagination import paginate
from app.crud.overview import build_overview

# --- Metrics ---

from app.models.metric import (
    CaseMetricUpdate, CaseMetricPublic, Metric, MetricCreate, MetricUpdate, MetricPublic, CaseMetricValue,
)
from app.models.dashboard import (
    Dashboard,
    DashboardCreate,
    DashboardUpdate,
    DashboardPublic,
    DashboardShareToken,
    PublicDashboardView,
)
from app.models.common import Page

router = APIRouter(prefix="/metrics", tags=["metrics"])
case_metrics_router = APIRouter(
    prefix="/cases/{case_id}/metrics", tags=["metrics"]
)


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

@case_metrics_router.get("", response_model=list[CaseMetricPublic])
@router.get("/cases/{case_id}/metrics", response_model=list[CaseMetricPublic])
async def list_case_metrics(
    case_id: int,
    ctx: ActiveOrgContext,
    db=Depends(get_session),
    _case_perm: CaseAuthContext = require_case_permission("read:case"),
):
    result = await db.execute(select(CaseMetricValue).where(CaseMetricValue.case_id == case_id))
    vals = result.scalars().all()
    out = []
    for v in vals:
        m = await db.get(Metric, v.metric_id)
        out.append(CaseMetricPublic(metric_id=v.metric_id, metric_name=m.name if m else "?", value=v.value, updated_at=v.updated_at))
    return out


@case_metrics_router.put("", response_model=list[CaseMetricPublic])
@router.put("/cases/{case_id}/metrics", response_model=list[CaseMetricPublic])
async def update_case_metrics(
    case_id: int,
    body: CaseMetricUpdate,
    ctx: ActiveOrgContext,
    db=Depends(get_session),
    _case_perm: CaseAuthContext = require_case_permission("write:case"),
):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for mid, val in body.metrics.items():
        metric_id = uuid.UUID(mid)
        # Validate metric belongs to the active organisation
        m = await db.get(Metric, metric_id)
        if not m or m.organisation_id != ctx.organisation_id:
            raise HTTPException(404, f"Metric {mid} not found")
        row = CaseMetricValue(
            case_id=case_id,
            metric_id=metric_id,
            value=val,
            updated_at=now,
        )
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
#
# Dashboards are "views" — a saved widget layout. Ownership and sharing reuse
# the existing columns rather than a migration:
#   * `created_by`  → owner user id (private dashboards are visible only to them)
#   * `is_public`   → shared with the whole organisation (read-only for others)
# Any org member may create their own dashboards; only the owner (or a
# superadmin) may edit, share or delete one.

dash_router = APIRouter(prefix="/dashboards", tags=["dashboards"])


def _owns(dashboard: Dashboard, ctx: ActiveOrgContext) -> bool:
    return dashboard.created_by == str(ctx.user.id) or ctx.user.is_superadmin


def _public(dashboard: Dashboard, ctx: ActiveOrgContext, owner_name: str | None) -> DashboardPublic:
    return DashboardPublic(
        id=dashboard.id,
        name=dashboard.name,
        description=dashboard.description,
        layout=dashboard.layout,
        is_public=dashboard.is_public,
        organisation_id=dashboard.organisation_id,
        created_by=dashboard.created_by,
        is_owner=_owns(dashboard, ctx),
        owner_name=owner_name,
        share_enabled=dashboard.share_token_hash is not None,
        created_at=dashboard.created_at,
        updated_at=dashboard.updated_at,
    )


def _hash_share_token(token: str) -> str:
    """SHA-256 hex of a share token. Tokens are 256-bit random, so a plain hash
    (no per-token salt) is sufficient — brute-forcing the preimage is infeasible
    and the hash is only ever compared for equality on lookup."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _load_owned(dashboard_id: uuid.UUID, ctx: ActiveOrgContext, db) -> Dashboard:
    """Fetch a dashboard the caller may mutate (owner or superadmin), or 404/403."""
    d = await db.get(Dashboard, dashboard_id)
    if not d or d.organisation_id != ctx.organisation_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not _owns(d, ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can modify this dashboard")
    return d


async def _owner_names(db, dashboards: list[Dashboard]) -> dict[str, str]:
    from app.models.user import User

    ids = {d.created_by for d in dashboards if d.created_by}
    parsed = {i: uuid.UUID(i) for i in ids if _is_uuid(i)}
    if not parsed:
        return {}
    rows = (
        await db.execute(
            sa_select(User.id, User.first_name, User.last_name).where(
                User.id.in_(list(parsed.values()))
            )
        )
    ).all()
    by_uuid = {uid: f"{fn} {ln}" for uid, fn, ln in rows}
    return {raw: by_uuid[uid] for raw, uid in parsed.items() if uid in by_uuid}


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


@dash_router.get("", response_model=list[DashboardPublic])
async def list_dashboards(ctx: ActiveOrgContext, db=Depends(get_session)):
    """The caller's own dashboards plus any shared with their organisation.
    Own dashboards first, then shared, alphabetical within each."""
    result = await db.execute(
        select(Dashboard).where(
            Dashboard.organisation_id == ctx.organisation_id,
            sa_or(
                Dashboard.created_by == str(ctx.user.id),
                Dashboard.is_public.is_(True),
            ),
        )
    )
    dashboards = list(result.scalars().all())
    names = await _owner_names(db, dashboards)
    dashboards.sort(key=lambda d: (d.created_by != str(ctx.user.id), d.name.lower()))
    return [_public(d, ctx, names.get(d.created_by)) for d in dashboards]


@dash_router.post("", response_model=DashboardPublic, status_code=201)
async def create_dashboard(body: DashboardCreate, ctx: ActiveOrgContext, db=Depends(get_session)):
    """Any org member may create a dashboard; it is owned by (and, by default,
    private to) the creator."""
    d = Dashboard(
        organisation_id=ctx.organisation_id,
        name=body.name,
        description=body.description,
        layout=body.layout,
        is_public=body.is_public,
        created_by=str(ctx.user.id),
    )
    db.add(d)
    await db.flush()
    names = await _owner_names(db, [d])
    return _public(d, ctx, names.get(d.created_by))


@dash_router.patch("/{dashboard_id}", response_model=DashboardPublic)
async def update_dashboard(dashboard_id: uuid.UUID, body: DashboardUpdate, ctx: ActiveOrgContext, db=Depends(get_session)):
    d = await _load_owned(dashboard_id, ctx, db)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    d.updated_by = str(ctx.user.id)
    db.add(d)
    await db.flush()
    names = await _owner_names(db, [d])
    return _public(d, ctx, names.get(d.created_by))


@dash_router.delete("/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    d = await _load_owned(dashboard_id, ctx, db)
    await db.delete(d)
    await db.flush()


@dash_router.post("/{dashboard_id}/share", response_model=DashboardShareToken)
async def create_share_link(dashboard_id: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    """Mint (or rotate) the read-only public share token for a dashboard. Only
    the owner may share. The plaintext token is returned exactly once here — only
    its hash is stored — so re-minting rotates and invalidates the previous link."""
    d = await _load_owned(dashboard_id, ctx, db)
    token = secrets.token_urlsafe(32)
    d.share_token_hash = _hash_share_token(token)
    d.updated_by = str(ctx.user.id)
    db.add(d)
    await db.flush()
    return DashboardShareToken(token=token)


@dash_router.delete("/{dashboard_id}/share", status_code=204)
async def revoke_share_link(dashboard_id: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    """Revoke the public share link. Idempotent — revoking an unshared dashboard
    is a no-op success."""
    d = await _load_owned(dashboard_id, ctx, db)
    d.share_token_hash = None
    d.updated_by = str(ctx.user.id)
    db.add(d)
    await db.flush()


# --- Public (unauthenticated) share view ---

public_dash_router = APIRouter(prefix="/public/dashboards", tags=["dashboards"])


@public_dash_router.get("/{token}", response_model=PublicDashboardView)
async def public_dashboard(
    token: str,
    trend_days: int = Query(default=14, ge=1, le=365),
    db=Depends(get_session),
):
    """Render a shared dashboard with no authentication. The token is hashed and
    matched against ``share_token_hash``; a miss (or a revoked link) is an
    indistinguishable 404. Only the board layout and the org-scoped overview
    aggregates are returned — never owner/tenancy metadata."""
    token_hash = _hash_share_token(token)
    d = (
        await db.execute(
            select(Dashboard).where(Dashboard.share_token_hash == token_hash)
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return PublicDashboardView(
        name=d.name,
        description=d.description,
        layout=d.layout,
        overview=await build_overview(db, d.organisation_id, trend_days=trend_days),
    )
