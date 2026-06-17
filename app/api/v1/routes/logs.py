import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.api.v1.routes._files import attach_blob, ingest_upload, stream_blob
from app.api.v1.routes.tasks import _require, _resolve_task_visibility
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import attachment as attachment_crud
from app.crud import log as log_crud
from app.models.attachment import AttachmentOwnerType, AttachmentPublic
from app.models.common import Page
from app.models.log import Log, LogPublic, LogUpdate

router = APIRouter(prefix="/logs", tags=["logs"])


async def _resolve_log(
    session: AsyncSession,
    ctx: ActiveOrgContext,
    log_id: uuid.UUID,
) -> tuple[Log, bool, set[str]]:
    log = await session.get(Log, log_id)
    if not log or log.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log not found")
    _, is_owner, perms = await _resolve_task_visibility(session, ctx, log.task_id)
    return log, is_owner, perms


@router.get("/{log_id}", response_model=LogPublic)
async def get_log(
    log_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    log, _, perms = await _resolve_log(session, ctx, log_id)
    _require("read:task", perms)
    return log


@router.patch("/{log_id}", response_model=LogPublic)
async def update_log(
    log_id: uuid.UUID,
    log_in: LogUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LogPublic:
    log, _, perms = await _resolve_log(session, ctx, log_id)
    _require("write:task", perms)
    return await log_crud.update_log(session, log, log_in, updated_by=str(ctx.user.id))


@router.delete("/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log(
    log_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    log, is_owner, perms = await _resolve_log(session, ctx, log_id)
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
    log_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> AttachmentPublic:
    log, _, perms = await _resolve_log(session, ctx, log_id)
    _require("write:task", perms)
    sha256, size, content_type = await ingest_upload(storage, file)
    link = await attach_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        owner_type=AttachmentOwnerType.log,
        owner_id=str(log.id),
        name=name or file.filename or sha256,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return attachment_crud.to_public(link, blob)


@router.get("/{log_id}/attachments", response_model=Page[AttachmentPublic])
async def list_log_attachments(
    log_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[AttachmentPublic]:
    log, _, perms = await _resolve_log(session, ctx, log_id)
    _require("read:task", perms)
    rows, total = await attachment_crud.list_links_for_owner(
        session, AttachmentOwnerType.log, str(log.id), skip=skip, limit=limit
    )
    return Page(
        items=[attachment_crud.to_public(link, blob) for link, blob in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{log_id}/attachments/{link_id}/file")
async def download_log_attachment(
    log_id: uuid.UUID,
    link_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    log, _, perms = await _resolve_log(session, ctx, log_id)
    _require("read:task", perms)
    link = await attachment_crud.get_link(session, link_id)
    if (
        link is None
        or link.owner_type != AttachmentOwnerType.log
        or link.owner_id != str(log.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return stream_blob(storage, link, blob)


@router.delete("/{log_id}/attachments/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_log_attachment(
    log_id: uuid.UUID,
    link_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    log, is_owner, perms = await _resolve_log(session, ctx, log_id)
    _require("write:task", perms)
    link = await attachment_crud.get_link(session, link_id)
    if (
        link is None
        or link.owner_type != AttachmentOwnerType.log
        or link.owner_id != str(log.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    if not (is_owner or link.organisation_id == ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the uploader org or case owner can delete this attachment",
        )
    await attachment_crud.delete_link(session, link, deleted_by=str(ctx.user.id))
