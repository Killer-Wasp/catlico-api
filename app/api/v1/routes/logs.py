from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.api.v1.routes._files import attach_case_blob, ingest_upload, stream_blob
from app.api.v1.routes.tasks import _require, _resolve_task_visibility
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import log as log_crud
from app.models.attachment import AttachmentPublic
from app.models.common import Page
from app.models.log import Log, LogPublic, LogUpdate

# Worklogs are nested under their task (and case): identity is (case_id, task_id, id).
router = APIRouter(prefix="/cases/{case_id}/tasks/{task_id}/logs", tags=["logs"])


async def _resolve_log(
    session: AsyncSession,
    ctx: ActiveOrgContext,
    case_id: int,
    task_id: int,
    log_id: int,
) -> tuple[Log, bool, set[str]]:
    # Task visibility (with task_share for non-owners) gates the log too.
    _, is_owner, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    log = await log_crud.get_log(session, case_id, task_id, log_id)
    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log not found")
    return log, is_owner, perms


@router.get("/{log_id}", response_model=LogPublic)
async def get_log(
    case_id: int,
    task_id: int,
    log_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    log, _, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("read:task", perms)
    return log


@router.patch("/{log_id}", response_model=LogPublic)
async def update_log(
    case_id: int,
    task_id: int,
    log_id: int,
    log_in: LogUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    log, _, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("write:task", perms)
    return await log_crud.update_log(session, log, log_in, updated_by=str(ctx.user.id))


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    case_id: int,
    task_id: int,
    log_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    log, is_owner, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("write:task", perms)
    if not (is_owner or log.organisation_id == ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator org or case owner can delete this log",
        )
    await log_crud.delete_log(session, log, deleted_by=str(ctx.user.id))


# --- File attachments on a log (uploading details to a task) ---

@router.post(
    "/{log_id}/attachments",
    response_model=AttachmentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def upload_log_attachment(
    case_id: int,
    task_id: int,
    log_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> AttachmentPublic:
    log, _, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("write:task", perms)
    sha256, size, content_type = await ingest_upload(storage, file)
    link = await attach_case_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        case_id=case_id,
        owner_task_id=task_id,
        owner_log_id=log_id,
        name=name or file.filename or sha256,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return attachment_crud.to_public(link, blob)


@router.get("/{log_id}/attachments", response_model=Page[AttachmentPublic])
async def list_log_attachments(
    case_id: int,
    task_id: int,
    log_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[AttachmentPublic]:
    await _resolve_log(session, ctx, case_id, task_id, log_id)
    _, _, perms = await _resolve_task_visibility(session, ctx, case_id, task_id)
    _require("read:task", perms)
    rows, total = await attachment_crud.list_links(
        session,
        case_id,
        owner_task_id=task_id,
        owner_log_id=log_id,
        skip=skip,
        limit=limit,
    )
    return Page(
        items=[attachment_crud.to_public(link, blob) for link, blob in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{log_id}/attachments/{attachment_id}/file")
async def download_log_attachment(
    case_id: int,
    task_id: int,
    log_id: int,
    attachment_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    log, _, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("read:task", perms)
    link = await attachment_crud.get_link(session, case_id, attachment_id)
    if link is None or link.owner_log_id != log_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return stream_blob(storage, link, blob)


@router.delete("/{log_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log_attachment(
    case_id: int,
    task_id: int,
    log_id: int,
    attachment_id: int,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    log, is_owner, perms = await _resolve_log(session, ctx, case_id, task_id, log_id)
    _require("write:task", perms)
    link = await attachment_crud.get_link(session, case_id, attachment_id)
    if link is None or link.owner_log_id != log_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    if not (is_owner or link.organisation_id == ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the uploader org or case owner can delete this attachment",
        )
    await attachment_crud.delete_link(session, link, deleted_by=str(ctx.user.id))
