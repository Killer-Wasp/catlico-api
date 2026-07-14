"""Plugin proposed actions: canonical-entity mutations a plugin requests.

Plugins enrich by default (``PluginResult``). Risky canonical edits — patching a
case, adding a tag, creating a task — are not applied directly by plugin code.
They become ``PluginProposedAction`` rows that an analyst approves (or that org
policy auto-applies). Approval applies the change through the same CRUD services
a user action uses, so audit/activity/outbox behaviour matches a human edit.

The applied actor combines both parties: ``plugin:<id>@<version> approved-by
user:<uid>``.
"""
import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
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

logger = logging.getLogger(__name__)

# Canonical action types a plugin may propose (mirrors the plan's enum).
#
# `execute_responder_action` was dropped (2026-07-15): it had no producer and no
# executor — a dead enum member with no post-approval path from a proposal to a
# responder run. Responders are now dispatched directly via on-demand manual runs
# (POST /cases|alerts/{id}/plugin-runs), not through the proposed-action pipeline.
# Re-add it only if proposal-chaining (a plugin proposing that another plugin run)
# is ever designed.
ACTION_TYPES = {
    "add_tag",
    "create_task",
    "append_task_log",
    "add_related_observable",
    "change_severity_status",
    "patch_case_description",
    "patch_observable",
}

# Action types this version knows how to apply. The others are accepted as
# proposals but reject on approval with a clear message until implemented.
_APPLICABLE = {
    "add_tag",
    "create_task",
    "append_task_log",
    "add_related_observable",
    "change_severity_status",
    "patch_case_description",
    "patch_observable",
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
        "patch_observable": "write:observable",
    }[action_type]


async def _existing_by_fingerprint(
    session: AsyncSession, run_id: uuid.UUID, fingerprint: str
) -> PluginProposedAction | None:
    """The proposal already recorded on ``run_id`` for ``fingerprint``, if any.

    Backs both the fast-path pre-check and the post-IntegrityError re-select in
    ``create`` so the two agree on the dedup key.
    """
    return (
        await session.execute(
            select(PluginProposedAction).where(
                PluginProposedAction.plugin_run_id == run_id,
                PluginProposedAction.fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()


async def create(
    session: AsyncSession,
    *,
    run: PluginRun,
    action_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict,
    fingerprint: str | None = None,
) -> PluginProposedAction:
    """Record a plugin-proposed mutation awaiting approval.

    Idempotency mirrors ``add_result``: when a non-None ``fingerprint`` is given,
    an existing proposal from the SAME run with the same fingerprint is returned
    unchanged — no new row, and no re-apply of an already auto-applied action.
    This makes redelivered events / retried runs (which reuse the run row) safe.
    """
    if action_type not in ACTION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown proposed action_type: {action_type}",
        )
    if fingerprint is not None:
        existing = await _existing_by_fingerprint(session, run.id, fingerprint)
        if existing is not None:
            # Already proposed on this run (redelivery/retry). Return it as-is —
            # do not insert again and do not re-run the auto-apply policy on an
            # action that may already be applied.
            return existing
    action = PluginProposedAction(
        plugin_run_id=run.id,
        organisation_id=run.organisation_id,
        plugin_id=run.plugin_id,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        fingerprint=fingerprint,
        payload=payload,
        status="proposed",
        created_by=run.plugin_id,
    )
    try:
        # Insert under a savepoint: the fast-path pre-check above is a TOCTOU, so a
        # concurrent duplicate that beat us to the flush trips
        # uq_plugin_proposed_action_run_fingerprint. Without the savepoint that
        # IntegrityError aborts the outer transaction and surfaces as a 500;
        # rolling back to the savepoint keeps it usable so we can re-select the
        # winner and resolve to it (mirrors the add_related_observable TOCTOU
        # handling below). This re-select-and-return path deliberately does NOT
        # fall through to auto-apply — the winning row already ran that policy.
        async with session.begin_nested():
            session.add(action)
            await session.flush()
    except IntegrityError:
        if fingerprint is not None:
            existing = await _existing_by_fingerprint(session, run.id, fingerprint)
            if existing is not None:
                return existing
        # Not the fingerprint dedup race (or no fingerprint to resolve against):
        # a genuine constraint violation the caller must handle.
        raise

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
                # Mirror decide(): run apply() inside a savepoint so a genuine
                # IntegrityError (any constraint other than the observable dedup
                # race, which add_related_observable resolves in its own inner
                # savepoint) rolls back to the SAVEPOINT instead of poisoning the
                # outer transaction. Without it, the terminal-status flush below
                # would itself fail on the aborted transaction and surface as an
                # unhandled 500 with the row stuck "proposed".
                async with session.begin_nested():
                    await apply(session, action, approver_user_id=None)
            except HTTPException:
                # Best-effort: fall back to analyst approval on validation
                # failures (target gone, unsupported type). The savepoint
                # rollback leaves the row "proposed" for later approval.
                return action
            except IntegrityError as exc:
                # Generic backstop mirroring decide(): an unexpected constraint
                # violation is a real failure, so record a terminal "failed"
                # status. decision_reason is exposed by public(), so keep it
                # generic and log the raw driver detail server-side instead of
                # leaking column/constraint names or SQL to API consumers.
                logger.warning(
                    "Auto-apply of proposed action %s failed: database integrity "
                    "error: %s",
                    action.id,
                    exc.orig,
                )
                action.status = "failed"
                action.decided_by = "system:auto-apply"
                action.decided_at = datetime.now(UTC)
                action.decision_reason = (
                    "Could not apply: the change conflicted with existing data "
                    "(database integrity constraint)."
                )
                await session.flush()
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


async def _observable_in_org_or_404(
    session: AsyncSession, organisation_id: str, observable_id: uuid.UUID
):
    """Fetch a live observable the org is entitled to *mutate*, else 404.

    Reuses the canonical org-visibility predicate (``_visible_observable_condition``:
    case the org owns, an observable shared to it, or an alert it owns) — the same
    rule the org observable list/facets use. This is intentionally the write
    boundary: a non-owner collaborator on a shared case may *propose* a patch (the
    runtime read check is looser) but its approval fails here, which is the correct
    authorization outcome for a mutation of another org's observable.
    """
    from app.models.observable import Observable

    obs = await obs_crud.get_observable(session, observable_id)
    if obs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found")
    visible = (
        await session.execute(
            select(Observable.id).where(
                Observable.id == observable_id,
                obs_crud._visible_observable_condition(organisation_id),
            )
        )
    ).scalar_one_or_none()
    if visible is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found")
    return obs


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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
        await case_crud.update_case(
            session,
            case,
            update,
            updated_by=actor,
            organisation_id=action.organisation_id,
        )
        return

    if action_type == "patch_observable":
        from app.models.observable import ObservableUpdate

        obs = await _observable_in_org_or_404(
            session, action.organisation_id, uuid.UUID(action.entity_id)
        )
        # Only the analyst-meaningful fields a plugin may propose; anything else
        # in the payload is ignored (the endpoint already filters, this is the
        # apply-side backstop so a hand-crafted proposal can't reach other columns).
        allowed = {"message", "ioc", "sighted"}
        update = ObservableUpdate(**{k: v for k, v in payload.items() if k in allowed})
        await obs_crud.update_observable(session, obs, update, updated_by=actor)
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=type_error
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
            #
            # The find/create above is a TOCTOU: a concurrent approval of the
            # same observable can insert the row between our find and our create,
            # and the partial unique index (uq_observable_case_dedup) then rejects
            # the loser with an IntegrityError. Run the insert in a savepoint so a
            # collision rolls back cleanly without poisoning the surrounding
            # transaction, then re-check.
            try:
                async with session.begin_nested():
                    await obs_crud.create_case_observable(
                        session,
                        obs_in,
                        case_id=case_id,
                        organisation_id=action.organisation_id,
                        created_by=actor,
                    )
            except IntegrityError:
                # Re-check after rolling back to the savepoint. If the row now
                # exists, a concurrent approver won the race and the proposal is
                # already satisfied — the same "already linked" no-op the
                # short-circuit above declares. If it still doesn't exist, the
                # IntegrityError came from some other constraint (not the dedup
                # race), which is a genuine failure: re-raise for decide()'s
                # backstop to mark the action failed.
                existing = await obs_crud.find_case_observable(
                    session, case_id, obs_in.observable_type, obs_in.data
                )
                if existing is None:
                    raise
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
        # Run apply() inside a savepoint. A genuine IntegrityError (any constraint
        # other than the observable dedup race, which the add_related_observable
        # branch resolves itself) aborts the current transaction, so without a
        # savepoint the status="failed" flush below would itself fail on the
        # poisoned transaction and surface as an unhandled 500 with the action
        # stuck in "proposed". Rolling back to the savepoint leaves the outer
        # transaction usable to record the terminal status.
        async with session.begin_nested():
            await apply(session, action, approver_user_id=approver_user_id)
    except HTTPException as exc:
        action.status = "failed"
        action.decision_reason = str(exc.detail)
        await session.flush()
        return action
    except IntegrityError as exc:
        # Generic backstop: an unexpected constraint violation is a real failure,
        # not a success. Terminal status, no 500. The reason is persisted and
        # returned by the public API, so keep it informative-but-generic — the
        # raw driver text (column/constraint names, SQL) is logged server-side
        # for operators instead of leaked to API consumers.
        logger.warning(
            "Proposed action %s failed to apply: database integrity error: %s",
            action.id,
            exc.orig,
        )
        action.status = "failed"
        action.decision_reason = (
            "Could not apply: the change conflicted with existing data "
            "(database integrity constraint)."
        )
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
