"""Plugin runtime internal endpoints — reads and mutations for plugin code.

These endpoints are called by plugin runs through the internal runtime API.
They require a run-scoped token and enforce plugin permissions, org scope,
and TLP/PAP constraints.
"""
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import PluginRuntime, PluginRuntimePrincipal
from app.core.configs import settings
from app.core.db import get_session
from app.core.storage import BlobStorage, UploadTooLarge, get_storage, save_upload
from app.crud import attachment as attachment_crud
from app.crud import audit as audit_crud
from app.crud import alert as alert_crud
from app.crud import case_ as case_crud
from app.crud import comment as comment_crud
from app.crud import observable as obs_crud
from app.crud import plugin_proposed_action as ppa_crud
from app.crud import task as task_crud
from app.crud.case_share import get_share
from app.models.observable import ObservableShare
from app.models.plugin_runner import PluginResult, PluginRun, PluginRunFile

router = APIRouter(prefix="/plugin-runtime", tags=["plugin-runtime"])


def _require(principal: PluginRuntimePrincipal, permission: str) -> None:
    if permission not in principal.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing plugin permission: {permission}",
        )


def _require_any(principal: PluginRuntimePrincipal, permissions: set[str]) -> None:
    if principal.permissions.isdisjoint(permissions):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing one of plugin permissions: {', '.join(sorted(permissions))}",
        )


async def _case_for_runtime(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    case_id: int,
):
    case = await case_crud.get_case(session, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )
    share = await get_share(session, case_id, principal.organisation_id)
    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
        )
    return case


async def _alert_for_runtime(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    alert_id: int,
):
    alert = await alert_crud.get_alert(session, alert_id)
    if alert is None or alert.organisation_id != principal.organisation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found"
        )
    return alert


async def _observable_for_runtime(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    observable_id: uuid.UUID,
):
    obs = await obs_crud.get_observable(session, observable_id)
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
        )
    if obs.alert_id is not None:
        await _alert_for_runtime(session, principal, obs.alert_id)
        return obs
    if obs.case_id is not None:
        share = await get_share(session, obs.case_id, principal.organisation_id)
        if share is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
            )
        if not share.is_owner:
            shared = await session.get(
                ObservableShare, (observable_id, principal.organisation_id)
            )
            if shared is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
                )
        return obs
    if obs.organisation_id != principal.organisation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Observable not found"
        )
    return obs


async def _task_for_runtime(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    case_id: int,
    task_id: int,
):
    await _case_for_runtime(session, principal, case_id)
    task = await task_crud.get_task(session, case_id, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return task


def _observable_context(obs) -> tuple[str, str]:
    if obs.case_id is not None:
        return "case", str(obs.case_id)
    if obs.alert_id is not None:
        return "alert", str(obs.alert_id)
    return "observable", str(obs.id)


# --- Reads ---


@router.get("/cases/{case_id}")
async def get_case(
    case_id: int,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "read:case")
    case = await _case_for_runtime(session, principal, case_id)
    return {
        "id": case.id,
        "title": case.title,
        "description": case.description,
        "severity": case.severity,
        "status": case.status,
        "tlp": case.tlp,
        "pap": case.pap,
    }


@router.get("/alerts/{alert_id}")
async def get_alert(
    alert_id: int,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "read:alert")
    alert = await _alert_for_runtime(session, principal, alert_id)
    return {
        "id": alert.id,
        "type": alert.type,
        "source": alert.source,
        "source_ref": alert.source_ref,
        "title": alert.title,
        "description": alert.description,
        "severity": alert.severity,
        "status": alert.status,
        "tlp": alert.tlp,
        "pap": alert.pap,
        "organisation_id": alert.organisation_id,
        "case_id": alert.case_id,
    }


@router.get("/observables/{observable_id}")
async def get_observable(
    observable_id: uuid.UUID,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "read:observable")
    obs = await _observable_for_runtime(session, principal, observable_id)
    return {
        "id": str(obs.id),
        "observable_type": obs.observable_type,
        "data": obs.data,
        "tlp": obs.tlp,
        "message": obs.message,
        "ioc": obs.ioc,
        "sighted": obs.sighted,
        "organisation_id": obs.organisation_id,
    }


# --- Mutations ---


async def _validate_result_entity(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    entity_type: str,
    entity_id: str,
) -> str:
    """Validate the target entity and return the canonical stored ``entity_id``.

    Task ids are only unique per case (``Case.next_task_seq``), so a bare task id
    stored as ``entity_id`` would collide across cases — and across organisations —
    on the read side. Tasks are therefore stored as ``"{case_id}:{task_id}"``; the
    other entity types have globally unique ids and are stored as-is.
    """
    if entity_type == "observable":
        await _observable_for_runtime(session, principal, uuid.UUID(entity_id))
        return entity_id
    if entity_type == "case":
        await _case_for_runtime(session, principal, int(entity_id))
        return entity_id
    if entity_type == "alert":
        await _alert_for_runtime(session, principal, int(entity_id))
        return entity_id
    if entity_type == "task":
        case_id = principal.event_object_id if principal.event_object_type == "case" else None
        if not case_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task results require a case event context",
            )
        await _task_for_runtime(session, principal, int(case_id), int(entity_id))
        return f"{int(case_id)}:{int(entity_id)}"
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Unsupported plugin result entity_type",
    )


async def _validate_result_attachments(
    session: AsyncSession,
    principal: PluginRuntimePrincipal,
    attachments: list[dict],
) -> None:
    if not isinstance(attachments, list):
        raise HTTPException(
            status_code=422,
            detail="attachments must be a list",
        )
    for item in attachments:
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=422,
                detail="attachments must contain objects",
            )
        file_ref = item.get("file_ref")
        if not file_ref:
            continue
        if not isinstance(file_ref, str) or not file_ref.startswith("plugin-run-file:"):
            raise HTTPException(
                status_code=422,
                detail="Unsupported attachment file_ref",
            )
        try:
            file_id = uuid.UUID(file_ref.removeprefix("plugin-run-file:"))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid attachment file_ref",
            )
        run_file = await session.get(PluginRunFile, file_id)
        if (
            run_file is None
            or run_file.plugin_run_id != principal.run_id
            or run_file.organisation_id != principal.organisation_id
        ):
            raise HTTPException(
                status_code=422,
                detail="Attachment file_ref does not belong to this run",
            )


@router.post("/results")
async def add_result(
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require_any(principal, {"write:plugin_result", "write:observable_enrichment"})
    entity_type = body["entity_type"]
    entity_id = str(body["entity_id"])
    fingerprint = body.get("fingerprint")
    if not fingerprint:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fingerprint is required",
        )
    entity_id = await _validate_result_entity(session, principal, entity_type, entity_id)
    attachments = body.get("attachments", [])
    await _validate_result_attachments(session, principal, attachments)

    existing = (
        await session.execute(
            select(PluginResult).where(
                PluginResult.plugin_run_id == principal.run_id,
                PluginResult.fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"id": str(existing.id), "created": False}

    result = PluginResult(
        plugin_run_id=principal.run_id,
        organisation_id=principal.organisation_id,
        plugin_id=principal.plugin_id,
        plugin_version_id=principal.plugin_version_id,
        entity_type=entity_type,
        entity_id=entity_id,
        source=body.get("source", "plugin"),
        verdict=body.get("verdict"),
        confidence=body.get("confidence"),
        render_mode=body.get("render_mode", "json"),
        title=body.get("title"),
        summary=body.get("summary"),
        normalized_data=body.get("normalized_data"),
        raw_data=body.get("raw_data"),
        result_metadata=body.get("metadata"),
        attachments=attachments,
        fingerprint=fingerprint,
        expires_at=body.get("expires_at"),
    )
    session.add(result)
    await session.flush()
    return {"id": str(result.id), "created": True}


async def _runtime_file_usage(
    session: AsyncSession, principal: PluginRuntimePrincipal
) -> tuple[int, int]:
    count, total_size = (
        await session.execute(
            select(
                func.count(PluginRunFile.id),
                func.coalesce(func.sum(PluginRunFile.size), 0),
            ).where(PluginRunFile.plugin_run_id == principal.run_id)
        )
    ).one()
    return int(count), int(total_size)


@router.post("/files")
async def upload_file(
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
) -> dict:
    _require_any(principal, {"write:plugin_result", "write:observable_enrichment"})
    file_count, total_size = await _runtime_file_usage(session, principal)
    if file_count >= settings.PLUGIN_RUNTIME_FILE_MAX_COUNT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Plugin run file count limit exceeded",
        )
    try:
        sha256, size, content_type = await save_upload(
            storage, file, settings.PLUGIN_RUNTIME_FILE_MAX_BYTES
        )
    except UploadTooLarge:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "File exceeds the "
                f"{settings.PLUGIN_RUNTIME_FILE_MAX_BYTES}-byte plugin runtime limit"
            ),
        )
    if total_size + size > settings.PLUGIN_RUNTIME_FILE_MAX_TOTAL_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Plugin run total file bytes limit exceeded",
        )
    blob = await attachment_crud.get_or_create_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        created_by=principal.actor,
    )
    run_file = PluginRunFile(
        plugin_run_id=principal.run_id,
        organisation_id=principal.organisation_id,
        plugin_id=principal.plugin_id,
        attachment_id=blob.id,
        filename=file.filename or sha256,
        content_type=content_type,
        size=size,
        sha256=sha256,
    )
    session.add(run_file)
    await session.flush()
    return {
        "file_ref": f"plugin-run-file:{run_file.id}",
        "filename": run_file.filename,
        "content_type": run_file.content_type,
        "size": run_file.size,
        "sha256": run_file.sha256,
    }


@router.get("/files/{file_ref:path}")
async def download_file(
    file_ref: str,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    if not file_ref.startswith("observable:"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Runtime file not found",
        )
    try:
        observable_id = uuid.UUID(file_ref.removeprefix("observable:"))
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Invalid observable file ref",
        )
    if (
        principal.event_object_type != "observable"
        or principal.event_object_id != str(observable_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runtime token is not scoped to this file observable",
        )
    obs = await _observable_for_runtime(session, principal, observable_id)
    link_blob = await attachment_crud.first_observable_link(session, obs.id)
    if link_blob is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No file attached to observable",
        )
    link, blob = link_blob
    return StreamingResponse(
        storage.stream(blob.sha256),
        media_type=blob.content_type,
        headers={
            "Content-Length": str(blob.size),
            "Content-Disposition": f'attachment; filename="{link.name}"',
            "X-SHA256": blob.sha256,
        },
    )


@router.post("/progress")
async def update_progress(
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await session.get(PluginRun, principal.run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.progress_message = body.get("message")
    run.progress_percent = body.get("percent")
    run.progress_updated_at = datetime.now(UTC)
    await session.flush()
    return {
        "run_id": str(run.id),
        "progress_message": run.progress_message,
        "progress_percent": run.progress_percent,
    }


@router.post("/observables/{observable_id}/enrichments")
async def add_enrichment(
    observable_id: uuid.UUID,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "write:observable_enrichment")
    obs = await _observable_for_runtime(session, principal, observable_id)

    result = PluginResult(
        plugin_run_id=principal.run_id,
        organisation_id=principal.organisation_id,
        plugin_id=principal.plugin_id,
        plugin_version_id=principal.plugin_version_id,
        entity_type="observable",
        entity_id=str(observable_id),
        source=body.get("source", "plugin"),
        verdict=body.get("verdict"),
        confidence=body.get("confidence"),
        render_mode=body.get("render_mode", "json"),
        title=body.get("title"),
        summary=body.get("summary"),
        normalized_data=body.get("normalized_data", body.get("data")),
        raw_data=body.get("raw_data"),
        result_metadata=body.get("metadata"),
        attachments=body.get("attachments", []),
        fingerprint=body.get("fingerprint") or f"observable:{observable_id}:{body.get('source', 'plugin')}",
        expires_at=body.get("expires_at"),
    )
    session.add(result)
    await session.flush()
    context_type, context_id = _observable_context(obs)
    await audit_crud.record_audit(
        session,
        action="create",
        obj=result,
        context_type=context_type,
        context_id=context_id,
        actor=principal.actor,
        organisation_id=principal.organisation_id,
        details={
            "observable_id": str(observable_id),
            "plugin_id": principal.plugin_id,
            "plugin_version_id": principal.plugin_version_id,
            "plugin_run_id": str(principal.run_id),
            "source": result.source,
            "verdict": result.verdict,
        },
    )
    return {"id": str(result.id), "ok": True}


async def _run_or_409(
    session: AsyncSession, principal: PluginRuntimePrincipal
) -> PluginRun:
    run = await session.get(PluginRun, principal.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Run not found for token"
        )
    return run


def _proposed(action) -> dict:
    return {"proposed_action_id": str(action.id), "status": action.status}


@router.patch("/cases/{case_id}", status_code=status.HTTP_202_ACCEPTED)
async def patch_case(
    case_id: int,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Propose a case description/title patch. Canonical edits are not applied
    directly — an analyst approves the proposed action (see plan mutation model)."""
    _require(principal, "write:case")
    await _case_for_runtime(session, principal, case_id)
    run = await _run_or_409(session, principal)
    action = await ppa_crud.create(
        session,
        run=run,
        action_type="patch_case_description",
        entity_type="case",
        entity_id=str(case_id),
        payload={k: v for k, v in body.items() if k in ("title", "description")},
    )
    return _proposed(action)


@router.post("/cases/{case_id}/tasks", status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    case_id: int,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Propose task creation on a case (approval required before it is created)."""
    _require(principal, "write:task")
    await _case_for_runtime(session, principal, case_id)
    run = await _run_or_409(session, principal)
    action = await ppa_crud.create(
        session,
        run=run,
        action_type="create_task",
        entity_type="case",
        entity_id=str(case_id),
        payload={
            "title": body["title"],
            "description": body.get("description", ""),
            "group": body.get("group", ""),
        },
    )
    return _proposed(action)


@router.post("/cases/{case_id}/comments")
async def add_comment(
    case_id: int,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "write:case")
    await _case_for_runtime(session, principal, case_id)

    from app.models.comment import CommentCreate, CommentEntityType

    comment_in = CommentCreate(message=body["message"])
    await comment_crud.create_comment(
        session, comment_in,
        entity_type=CommentEntityType.case,
        entity_id=str(case_id),
        organisation_id=principal.organisation_id,
        created_by=principal.actor,
    )
    return {"ok": True}


@router.post("/cases/{case_id}/tasks/{task_id}/logs")
async def add_task_log(
    case_id: int,
    task_id: int,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "write:task")
    task = await _task_for_runtime(session, principal, case_id, task_id)

    from app.models.log import LogCreate

    log_in = LogCreate(message=body["message"])
    from app.crud import log as log_crud
    await log_crud.create_log(
        session, log_in,
        case_id=case_id,
        task_id=task.id,
        organisation_id=principal.organisation_id,
        created_by=principal.actor,
    )
    return {"ok": True}


@router.post("/cases/{case_id}/tags", status_code=status.HTTP_202_ACCEPTED)
async def add_tag(
    case_id: int,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Propose adding a tag to a case (approval required before it is applied)."""
    _require(principal, "write:case")
    await _case_for_runtime(session, principal, case_id)
    run = await _run_or_409(session, principal)
    action = await ppa_crud.create(
        session,
        run=run,
        action_type="add_tag",
        entity_type="case",
        entity_id=str(case_id),
        payload={"tag": body["tag"]},
    )
    return _proposed(action)


@router.patch("/observables/{observable_id}")
async def patch_observable(
    observable_id: uuid.UUID,
    body: dict,
    principal: PluginRuntime,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require(principal, "write:observable")
    obs = await _observable_for_runtime(session, principal, observable_id)

    from app.models.observable import ObservableUpdate

    update = ObservableUpdate(
        **{k: v for k, v in body.items() if k in ("message", "ioc", "sighted")}
    )
    await obs_crud.update_observable(session, obs, update, updated_by=principal.actor)
    return {"ok": True}
