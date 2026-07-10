"""Plugin proposed actions: canonical-entity mutations a plugin requests.

Plugins enrich by default (``PluginResult``). Risky canonical edits — patching a
case, adding a tag, creating a task — are not applied directly by plugin code.
They become ``PluginProposedAction`` rows that an analyst approves (or that org
policy auto-applies). Approval applies the change through the same CRUD services
a user action uses, so audit/activity/outbox behaviour matches a human edit.

The applied actor combines both parties: ``plugin:<id>@<version> approved-by
user:<uid>``.
"""
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import log as log_crud
from app.crud import observable as obs_crud
from app.crud import tag as tag_crud
from app.crud import task as task_crud
from app.crud.case_share import get_share
from app.models.plugin_runner import OrgPlugin, PluginProposedAction, PluginRun
from app.models.tag import TaggableType

# Canonical action types a plugin may propose (mirrors the plan's enum).
ACTION_TYPES = {
    "add_tag",
    "create_task",
    "append_task_log",
    "add_related_observable",
    "change_severity_status",
    "patch_case_description",
    "execute_responder_action",
}

# Action types this version knows how to apply. The others are accepted as
# proposals but reject on approval with a clear message until implemented.
#
# `execute_responder_action` is deliberately never added here: there is no
# post-approval execution path from a PluginProposedAction to a responder.
# Responder-style actions run through a separate pipeline entirely (connector
# jobs claimed/submitted via app/api/internal/routes/responder.py and applied
# by app/services/connector_operations.py), which the plugin runtime/runner
# has no callback into after a proposal is approved. Building that bridge is
# architectural work beyond this module, so approval fails explicitly instead
# of guessing at a mapping — see the dedicated message in `apply()` below.
_APPLICABLE = {
    "add_tag",
    "create_task",
    "append_task_log",
    "add_related_observable",
    "change_severity_status",
    "patch_case_description",
}

# Low-risk actions an org may opt into auto-applying (no analyst approval). The
# rest are always analyst-gated regardless of policy.
LOW_RISK_ACTIONS = {
    "add_tag",
    "create_task",
    "append_task_log",
    "add_related_observable",
}


def approve_permission(action_type: str, entity_type: str) -> str:
    """The permission the approving user must hold — the same one the equivalent
    manual action requires."""
    if action_type == "add_tag":
        return "write:observable" if entity_type == "observable" else "write:case"
    return {
        "create_task": "write:task",
        "append_task_log": "write:task",
        "add_related_observable": "write:observable",
        "change_severity_status": "write:case",
        "patch_case_description": "write:case",
        "execute_responder_action": "write:case",
    }[action_type]


async def create(
    session: AsyncSession,
    *,
    run: PluginRun,
    action_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict,
) -> PluginProposedAction:
    """Record a plugin-proposed mutation awaiting approval."""
    if action_type not in ACTION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown proposed action_type: {action_type}",
        )
    action = PluginProposedAction(
        plugin_run_id=run.id,
        organisation_id=run.organisation_id,
        plugin_id=run.plugin_id,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload=payload,
        status="proposed",
        created_by=run.plugin_id,
    )
    session.add(action)
    await session.flush()

    # Org auto-apply policy: low-risk actions the org opted into are applied
    # immediately (born applied). Anything else waits for analyst approval.
    if action_type in LOW_RISK_ACTIONS:
        org_plugin = await session.get(
            OrgPlugin, (run.organisation_id, run.plugin_id)
        )
        opted_in = org_plugin is not None and action_type in (
            org_plugin.auto_apply_actions or []
        )
        if opted_in:
            try:
                await apply(session, action, approver_user_id=None)
            except HTTPException:
                # Best-effort: fall back to analyst approval on failure.
                return action
            action.status = "applied"
            action.decided_by = "system:auto-apply"
            action.decided_at = datetime.now(UTC)
            await session.flush()
    return action


async def list_for_org(
    session: AsyncSession,
    organisation_id: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    plugin_id: str | None = None,
    status_filter: str | None = None,
) -> list[PluginProposedAction]:
    query = select(PluginProposedAction).where(
        PluginProposedAction.organisation_id == organisation_id
    )
    if entity_type is not None:
        query = query.where(PluginProposedAction.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(PluginProposedAction.entity_id == entity_id)
    if plugin_id is not None:
        query = query.where(PluginProposedAction.plugin_id == plugin_id)
    if status_filter is not None:
        query = query.where(PluginProposedAction.status == status_filter)
    query = query.order_by(PluginProposedAction.created_at.desc())
    return list((await session.execute(query)).scalars().all())


async def _plugin_actor(session: AsyncSession, action: PluginProposedAction) -> str:
    run = await session.get(PluginRun, action.plugin_run_id)
    version = ""
    if run is not None and run.plugin_version_id:
        version = run.plugin_version_id.removeprefix(f"{action.plugin_id}@")
    return f"plugin:{action.plugin_id}@{version}" if version else f"plugin:{action.plugin_id}"


async def _case_in_org_or_404(session: AsyncSession, organisation_id: str, case_id: int):
    case = await case_crud.get_case(session, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    share = await get_share(session, case_id, organisation_id)
    if share is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


async def apply(
    session: AsyncSession,
    action: PluginProposedAction,
    *,
    approver_user_id: str | None,
) -> None:
    """Apply an approved action through normal CRUD services.

    Raises HTTPException for validation problems (target gone, unsupported type)
    so the caller can mark the action ``failed`` with the message. The applied
    actor records the plugin plus the approving user (or auto-apply policy).
    """
    action_type = action.action_type
    if action_type not in _APPLICABLE:
        if action_type == "execute_responder_action":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "execute_responder_action cannot be approved: there is no "
                    "post-approval path from a proposed action to a responder "
                    "run. Responder actions execute through the separate "
                    "connector-job pipeline (claimed and submitted by the "
                    "konnect worker), not proposed-action approval."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Action type {action_type!r} cannot be applied yet",
        )
    payload = action.payload or {}
    plugin_actor = await _plugin_actor(session, action)
    if approver_user_id is not None:
        actor = f"{plugin_actor} approved-by user:{approver_user_id}"
    else:
        actor = f"{plugin_actor} auto-applied"

    if action_type in ("patch_case_description", "change_severity_status"):
        from app.models.case_ import CaseUpdate

        case = await _case_in_org_or_404(session, action.organisation_id, int(action.entity_id))
        if action_type == "patch_case_description":
            allowed = {"title", "description"}
        else:
            allowed = {"severity", "status"}
        update = CaseUpdate(**{k: v for k, v in payload.items() if k in allowed})
        await case_crud.update_case(session, case, update, updated_by=actor)
        return

    if action_type == "create_task":
        from app.models.task import TaskCreate

        await _case_in_org_or_404(session, action.organisation_id, int(action.entity_id))
        task_in = TaskCreate(
            title=payload["title"],
            description=payload.get("description", ""),
            group=payload.get("group", ""),
        )
        await task_crud.create_task(
            session,
            task_in,
            case_id=int(action.entity_id),
            organisation_id=action.organisation_id,
            created_by=actor,
        )
        return

    if action_type == "append_task_log":
        from app.models.log import LogCreate

        case_id = int(payload["case_id"])
        task_id = int(action.entity_id)
        await _case_in_org_or_404(session, action.organisation_id, case_id)
        task = await task_crud.get_task(session, case_id, task_id)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        await log_crud.create_log(
            session,
            LogCreate(message=payload["message"]),
            case_id=case_id,
            task_id=task_id,
            organisation_id=action.organisation_id,
            created_by=actor,
        )
        return

    if action_type == "add_related_observable":
        from app.models.observable import ObservableCreate

        if action.entity_type != "case":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "add_related_observable only supports entity_type 'case', "
                    f"got {action.entity_type!r}"
                ),
            )
        case_id = int(action.entity_id)
        await _case_in_org_or_404(session, action.organisation_id, case_id)
        obs_in = ObservableCreate(
            observable_type=payload["observable_type"],
            data=payload["data"],
            message=payload.get("message", ""),
            tlp=payload.get("tlp", 2),
            ioc=payload.get("ioc", False),
            sighted=payload.get("sighted", False),
            ignore_similarity=payload.get("ignore_similarity", False),
        )
        type_error = await obs_crud.check_creatable_type(session, obs_in.observable_type)
        if type_error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=type_error
            )
        # Idempotent "create/link": if this exact observable is already on the
        # case (matches the same within-case dedup key the manual create route
        # enforces), treat the proposal as already satisfied instead of
        # conflicting — a plugin re-proposing an observable that's already
        # linked isn't an error.
        existing = await obs_crud.find_case_observable(
            session, case_id, obs_in.observable_type, obs_in.data
        )
        if existing is None:
            # No enqueue_auto_for_observable here (unlike the manual create
            # route): derived/automated creation must not re-trigger enrichment
            # — matches enrichment.py, which also skips it — to avoid
            # plugin→observable→enrichment loops (plan invariant: plugin-actor
            # events are never dispatched).
            await obs_crud.create_case_observable(
                session,
                obs_in,
                case_id=case_id,
                organisation_id=action.organisation_id,
                created_by=actor,
            )
        return

    # add_tag — the only action type that reaches this fallthrough.
    case = await _case_in_org_or_404(session, action.organisation_id, int(action.entity_id))
    existing = await tag_crud.list_tag_strings_for(
        session, TaggableType.case, str(action.entity_id)
    )
    new_tags = list(existing) + [payload["tag"]]
    await tag_crud.set_tags(session, TaggableType.case, str(action.entity_id), new_tags)
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        context=case,
        actor=actor,
        organisation_id=action.organisation_id,
        details={"tags_added": [payload["tag"]]},
    )


async def decide(
    session: AsyncSession,
    action: PluginProposedAction,
    *,
    approve_action: bool,
    approver_user_id: str,
    reason: str | None = None,
) -> PluginProposedAction:
    """Approve (apply) or reject a proposed action. Sets terminal status."""
    if action.status != "proposed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Action is already {action.status}",
        )
    now = datetime.now(UTC)
    action.decided_by = approver_user_id
    action.decided_at = now
    if not approve_action:
        action.status = "rejected"
        action.decision_reason = reason
        await session.flush()
        return action
    try:
        await apply(session, action, approver_user_id=approver_user_id)
    except HTTPException as exc:
        action.status = "failed"
        action.decision_reason = str(exc.detail)
        await session.flush()
        return action
    action.status = "applied"
    action.decision_reason = reason
    await session.flush()
    return action


def public(action: PluginProposedAction) -> dict:
    return {
        "id": str(action.id),
        "plugin_id": action.plugin_id,
        "plugin_run_id": str(action.plugin_run_id),
        "action_type": action.action_type,
        "entity_type": action.entity_type,
        "entity_id": action.entity_id,
        "payload": action.payload,
        "status": action.status,
        "decision_reason": action.decision_reason,
        "decided_by": action.decided_by,
        "decided_at": action.decided_at.isoformat() if action.decided_at else None,
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }
