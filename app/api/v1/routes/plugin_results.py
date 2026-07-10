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
from fastapi.responses import StreamingResponse
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
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import plugin_result as pr_crud

router = APIRouter(tags=["plugin-results"])


def _require(permission: str, permissions: set[str]) -> None:
    if permission not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


def _sanitize_filename(name: str | None) -> str:
    """Reduce a plugin-authored filename to a header-safe basename.

    Plugin filenames are untrusted. We take the basename (dropping ``/`` and ``\\``
    path separators and any ``../`` traversal), strip control characters — including
    CR/LF and NUL, which would otherwise permit header injection — and remove the
    double-quote that delimits the ``Content-Disposition`` quoted-string. The result
    can never break out of ``filename="..."`` or split the header.
    """
    if not name:
        return "download"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]  # basename; kills traversal
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")  # drop controls
    name = name.replace('"', "").strip()
    if name in ("", ".", ".."):
        return "download"
    return name


async def _download_attachment(
    session: AsyncSession,
    storage: BlobStorage,
    entity_type: str,
    entity_id: str,
    file_ref: str,
) -> StreamingResponse:
    """Stream a plugin attachment for an entity the caller has already been authorized
    to read. ``file_ref`` is resolved through ``PluginRunFile`` and only served if a
    result on this entity references it (see ``resolve_entity_attachment``).

    Untrusted content is served defensively: ``Content-Disposition: attachment`` with
    a sanitized filename, a fixed ``application/octet-stream`` type (the plugin's
    declared ``content_type`` is never reflected — it could claim ``text/html`` and get
    script execution same-origin), and ``X-Content-Type-Options: nosniff``. Integrity
    is inherent: the blob store is content-addressed, so streaming by ``sha256`` can
    only return bytes whose hash is that key; ``X-SHA256`` lets clients re-verify.
    """
    run_file = await pr_crud.resolve_entity_attachment(
        session, entity_type, entity_id, file_ref
    )
    if run_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    blob = await attachment_crud.get_blob(session, run_file.attachment_id)
    if blob is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    filename = _sanitize_filename(run_file.filename)
    return StreamingResponse(
        storage.stream(blob.sha256),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(blob.size),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-SHA256": blob.sha256,
        },
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


@router.get("/observables/{observable_id}/plugin-results/files/{file_ref:path}")
async def download_observable_plugin_result_file(
    observable_id: uuid.UUID,
    file_ref: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Download a plugin attachment on an observable. Same guard as the list route
    above (``read:observable`` under the observable's share-intersected perms)."""
    _, _, perms = await _resolve_observable_visibility(session, ctx, observable_id)
    _require("read:observable", perms)
    return await _download_attachment(
        session, storage, "observable", str(observable_id), file_ref
    )


@router.get("/cases/{case_id}/plugin-results")
async def list_case_plugin_results(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    """Plugin results for a case. Reuses ``require_case_permission("read:case")`` —
    the same guard GET /cases/{id} case-scoped reads use (owner or CaseShare)."""
    results = await pr_crud.list_for_entity(session, "case", str(case_ctx.case.id))
    return pr_crud.serialize_list(results)


@router.get("/cases/{case_id}/plugin-results/files/{file_ref:path}")
async def download_case_plugin_result_file(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    file_ref: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Download a plugin attachment on a case. Same guard as the list route
    (``require_case_permission("read:case")`` — owner or CaseShare)."""
    return await _download_attachment(
        session, storage, "case", str(case_ctx.case.id), file_ref
    )


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


@router.get("/alerts/{alert_id}/plugin-results/files/{file_ref:path}")
async def download_alert_plugin_result_file(
    alert_id: int,
    file_ref: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Download a plugin attachment on an alert. Same guard as the list route: the
    active org must own the alert (no sharing) and hold ``read:alert``."""
    await _resolve_owned_alert(session, ctx, alert_id)
    _require("read:alert", ctx.permissions)
    return await _download_attachment(session, storage, "alert", str(alert_id), file_ref)


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


@router.get("/cases/{case_id}/tasks/{task_id}/plugin-results/files/{file_ref:path}")
async def download_task_plugin_result_file(
    case_id: int,
    task_id: int,
    file_ref: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Download a plugin attachment on a task. Same guard as the list route
    (``read:task`` under the case's share-intersected perms)."""
    _, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    return await _download_attachment(session, storage, "task", str(task_id), file_ref)
