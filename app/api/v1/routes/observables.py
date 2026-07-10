import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import ActiveOrgOrApiKeyContext
from app.api.v1.routes._files import stream_blob
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import observable as obs_crud
from app.crud import tag as tag_crud
from app.crud.case_share import get_share
from app.models.common import Page
from app.models.observable import (
    Observable,
    ObservableFacets,
    ObservablePublic,
    ObservableShare,
    ObservableUpdate,
)
from app.models.role import RolePermission
from app.models.tag import TaggableType, TagSetRequest

router = APIRouter(prefix="/observables", tags=["observables"])


async def _resolve_observable_visibility(
    session: AsyncSession,
    ctx: ActiveOrgOrApiKeyContext,
    observable_id: uuid.UUID,
) -> tuple[Observable, bool, set[str]]:
    """Return (observable, is_owner, effective_permissions) for the active org.
    Case observables ride case visibility (owner sees all; non-owner needs an
    observable_share; perms are the case_share-role intersection). Alert observables
    ride alert org-ownership (no sharing)."""
    obs = await obs_crud.get_observable(session, observable_id)
    if obs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found")

    if ctx.user.is_superadmin:
        return obs, True, ctx.permissions

    if obs.alert_id is not None:
        # Alert observable: visible iff active org owns the alert.
        from app.crud.alert import get_alert
        alert = await get_alert(session, obs.alert_id)
        if alert is None or alert.organisation_id != ctx.organisation_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
            )
        return obs, True, ctx.permissions

    # Case observable.
    share = await get_share(session, obs.case_id, ctx.organisation_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found")
    if not share.is_owner:
        shared = await session.get(ObservableShare, (observable_id, ctx.organisation_id))
        if shared is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
            )
    pinned = await session.execute(
        select(RolePermission.permission).where(RolePermission.role_id == share.role_id)
    )
    effective = ctx.permissions & set(pinned.scalars().all())
    return obs, share.is_owner, effective


def _require(perm: str, perms: set[str]) -> None:
    if perm not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {perm}",
        )


@router.get("/", response_model=Page[ObservablePublic])
async def list_observables(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    filter: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "",
    order: Annotated[str, Query()] = "desc",
) -> Page[ObservablePublic]:
    """Global observable list for the active org: case observables on cases it owns
    (owner sees all) plus observables explicitly shared to it, and observables on alerts
    it owns. Each `filter` term is `key~op~value` (keys: type, tlp, flag, source,
    value), OR-within-key / AND-across-key."""
    if "read:observable" not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing permission: read:observable",
        )
    filters = obs_crud.ObservableListFilter.from_query(filter, sort=sort, order=order)
    obs, total = await obs_crud.list_observables_for_org(
        session,
        organisation_id=ctx.organisation_id,
        skip=skip,
        limit=limit,
        filters=filters,
    )
    return Page(items=obs, total=total, skip=skip, limit=limit)


@router.get("/filters", response_model=ObservableFacets)
async def list_observable_filters(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservableFacets:
    """Distinct source values across the org's observables, for the list view's
    Source filter dropdown. Declared before `/{observable_id}` so the literal wins."""
    if "read:observable" not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing permission: read:observable",
        )
    return await obs_crud.observable_facets(session, ctx.organisation_id)


@router.get("/{observable_id}", response_model=ObservablePublic)
async def get_observable(
    observable_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservablePublic:
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("read:observable", perms)
    return obs


@router.patch("/{observable_id}", response_model=ObservablePublic)
async def update_observable(
    observable_id: uuid.UUID,
    obs_in: ObservableUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservablePublic:
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("write:observable", perms)
    return await obs_crud.update_observable(session, obs, obs_in, updated_by=str(ctx.user.id))


@router.delete("/{observable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_observable(
    observable_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    obs, is_owner, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("write:observable", perms)
    if not (is_owner or obs.organisation_id == ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator org or case owner can delete this observable",
        )
    await obs_crud.delete_observable(session, obs, deleted_by=str(ctx.user.id))


@router.get("/{observable_id}/file")
async def download_observable_file(
    observable_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("read:observable", perms)
    link_blob = await attachment_crud.first_observable_link(session, obs.id)
    if link_blob is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This observable has no file attachment",
        )
    link, blob = link_blob
    return stream_blob(storage, link, blob)


# --- Tags on an observable ---

@router.get("/{observable_id}/tags", response_model=list[str])
async def list_observable_tags(
    observable_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("read:observable", perms)
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.observable, str(obs.id)
    )


@router.put("/{observable_id}/tags", response_model=list[str])
async def set_observable_tags(
    observable_id: uuid.UUID,
    body: TagSetRequest,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("write:observable", perms)
    await tag_crud.set_tags(session, TaggableType.observable, str(obs.id), body.tags)
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.observable, str(obs.id)
    )


# --- Plugin runs ---

@router.post("/{observable_id}/plugin-runs")
async def run_plugin_for_observable(
    observable_id: uuid.UUID,
    body: dict,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    obs, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("run:enrichment", perms)
    from app.api.v1.routes.plugins import create_manual_plugin_run

    return await create_manual_plugin_run(
        session,
        ctx,
        plugin_id=body["plugin_id"],
        entity_type="observable",
        entity_id=str(obs.id),
    )
