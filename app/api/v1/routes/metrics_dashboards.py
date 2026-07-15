"""G3: Dashboards CRUD + routes.

(G2 custom metrics were removed 2026-07-14 in favor of custom fields —
mirroring TheHive's own deprecation of case metrics. See migration
``a9d1e3f5b7c9_drop_metrics``.)
"""

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select
from sqlmodel import select

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.core.extensions import registry
from app.crud.overview import build_overview


def require_dashboard_capability() -> None:
    """Gate the Dashboards (custom saved views) feature behind the ``dashboard``
    capability flag. The flag is install-gated: it is flipped on only by the
    enterprise extension, so in OSS (no extension) it is ``False`` and every
    dashboard route 404s — a true server-side gate, not mere UI hiding."""
    if not registry.capabilities().get("dashboard", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        )

from app.models.dashboard import (
    Dashboard,
    DashboardCreate,
    DashboardUpdate,
    DashboardPublic,
    DashboardShareToken,
    PublicDashboardView,
)


# --- Dashboards ---
#
# Dashboards are "views" — a saved widget layout. Ownership and sharing reuse
# the existing columns rather than a migration:
#   * `created_by`  → owner user id (private dashboards are visible only to them)
#   * `is_public`   → shared with the whole organisation (read-only for others)
# Any org member may create their own dashboards; only the owner (or a
# superadmin) may edit, share or delete one.

dash_router = APIRouter(
    prefix="/dashboards",
    tags=["dashboards"],
    dependencies=[Depends(require_dashboard_capability)],
)


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

public_dash_router = APIRouter(
    prefix="/public/dashboards",
    tags=["dashboards"],
    dependencies=[Depends(require_dashboard_capability)],
)


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
