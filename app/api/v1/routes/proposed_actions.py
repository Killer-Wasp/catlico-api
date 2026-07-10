"""Public API: analyst review of plugin-proposed canonical mutations.

Plugins propose risky edits (case patch, tag, task) rather than applying them.
Analysts list pending proposals and approve/reject. Approving applies the change
through normal CRUD services, recording both the plugin and the approving user.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import plugin_proposed_action as ppa_crud
from app.models.plugin_runner import PluginProposedAction
from app.services import plugin_audit

router = APIRouter(prefix="/proposed-actions", tags=["proposed-actions"])


def _require(permission: str, permissions: set[str]) -> None:
    if permission not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def _action_or_404(
    session: AsyncSession, action_id: uuid.UUID, organisation_id: str
) -> PluginProposedAction:
    action = await session.get(PluginProposedAction, action_id)
    if action is None or action.organisation_id != organisation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proposed action not found"
        )
    return action


@router.get("")
async def list_proposed_actions(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: str | None = None,
    entity_id: str | None = None,
    plugin_id: str | None = None,
    status_filter: str | None = None,
) -> list[dict]:
    _require("read:connector", ctx.permissions)
    actions = await ppa_crud.list_for_org(
        session,
        ctx.organisation_id,
        entity_type=entity_type,
        entity_id=entity_id,
        plugin_id=plugin_id,
        status_filter=status_filter,
    )
    return [ppa_crud.public(a) for a in actions]


@router.post("/{action_id}/approve")
async def approve_proposed_action(
    action_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    action = await _action_or_404(session, action_id, ctx.organisation_id)
    _require(
        ppa_crud.approve_permission(action.action_type, action.entity_type),
        ctx.permissions,
    )
    action = await ppa_crud.decide(
        session,
        action,
        approve_action=True,
        approver_user_id=str(ctx.user.id),
    )
    await plugin_audit.record_admin_action(
        session, action="approve", object_type="plugin_proposed_action",
        object_id=str(action.id), actor=str(ctx.user.id),
        organisation_id=ctx.organisation_id,
        details={
            "action_type": action.action_type,
            "plugin_id": action.plugin_id,
            "result_status": action.status,
        },
    )
    return ppa_crud.public(action)


@router.post("/{action_id}/reject")
async def reject_proposed_action(
    action_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    body: dict | None = None,
) -> dict:
    action = await _action_or_404(session, action_id, ctx.organisation_id)
    _require(
        ppa_crud.approve_permission(action.action_type, action.entity_type),
        ctx.permissions,
    )
    action = await ppa_crud.decide(
        session,
        action,
        approve_action=False,
        approver_user_id=str(ctx.user.id),
        reason=(body or {}).get("reason"),
    )
    await plugin_audit.record_admin_action(
        session, action="reject", object_type="plugin_proposed_action",
        object_id=str(action.id), actor=str(ctx.user.id),
        organisation_id=ctx.organisation_id,
        details={"action_type": action.action_type, "plugin_id": action.plugin_id},
    )
    return ppa_crud.public(action)
