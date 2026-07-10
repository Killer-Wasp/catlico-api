"""Public read surface for plugin results (the Plugin Results / Enrichment panel).

One list route per entity that can carry plugin evidence — observable, case, alert,
and task (the entity types written by ``POST /api/internal/plugin-runtime/results``
and the enrichment route). Each route reuses the *exact* entity-read guard the
canonical entity-read route uses, so a viewer sees plugin results for an entity iff
they can already read that entity (including CaseShare / ObservableShare / TaskShare
access). Multi-tenancy is enforced by that guard before the query runs, not by a
post-filter. Results are returned newest-first; see ``app/crud/plugin_result.py`` for
the staleness rule and (plugin_id, source) "latest" grouping.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ActiveOrgOrApiKeyContext,
    CaseAuthContext,
    require_case_permission,
)
from app.api.v1.routes.alerts import _resolve_owned_alert
from app.api.v1.routes.observables import _resolve_observable_visibility
from app.api.v1.routes.tasks import _resolve_task_visibility
from app.core.db import get_session
from app.crud import plugin_result as pr_crud

router = APIRouter(tags=["plugin-results"])


def _require(permission: str, permissions: set[str]) -> None:
    if permission not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


@router.get("/observables/{observable_id}/plugin-results")
async def list_observable_plugin_results(
    observable_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Plugin results for an observable. Mirrors GET /observables/{id}: requires
    ``read:observable`` under the observable's effective (share-intersected) perms."""
    _, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("read:observable", perms)
    results = await pr_crud.list_for_entity(session, "observable", str(observable_id))
    return pr_crud.serialize_list(results)


@router.get("/cases/{case_id}/plugin-results")
async def list_case_plugin_results(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Plugin results for a case. Reuses ``require_case_permission("read:case")`` —
    the same guard GET /cases/{id} case-scoped reads use (owner or CaseShare)."""
    results = await pr_crud.list_for_entity(session, "case", str(case_ctx.case.id))
    return pr_crud.serialize_list(results)


@router.get("/alerts/{alert_id}/plugin-results")
async def list_alert_plugin_results(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Plugin results for an alert. Mirrors GET /alerts/{id}: the active org must own
    the alert (alerts have no sharing) and hold ``read:alert``."""
    await _resolve_owned_alert(session, ctx, alert_id)
    _require("read:alert", ctx.permissions)
    results = await pr_crud.list_for_entity(session, "alert", str(alert_id))
    return pr_crud.serialize_list(results)


@router.get("/cases/{case_id}/tasks/{task_id}/plugin-results")
async def list_task_plugin_results(
    case_id: int,
    task_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Plugin results for a task. Mirrors GET /cases/{case_id}/tasks/{task_id}:
    requires ``read:task`` under the case's effective (share-intersected) perms."""
    _, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    results = await pr_crud.list_for_entity(session, "task", str(task_id))
    return pr_crud.serialize_list(results)
