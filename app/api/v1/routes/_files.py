"""Shared helpers for file uploads/downloads across observables and task logs."""
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.core.storage import BlobStorage, UploadTooLarge, save_upload
import uuid

from app.crud import attachment as attachment_crud
from app.crud import observable as obs_crud
from app.models.attachment import (
    Attachment,
    AttachmentLink,
    ObservableAttachmentLink,
)


async def assert_attachment_type(session: AsyncSession, type_name: str) -> None:
    """File endpoints require a registered attachment-backed type (e.g. 'file')."""
    t = await obs_crud.get_type(session, type_name)
    if t is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown observable type: {type_name}",
        )
    if not t.is_attachment:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"'{type_name}' is a string type — use the JSON observables endpoint",
        )


async def ingest_upload(
    storage: BlobStorage, upload: UploadFile
) -> tuple[str, int, str]:
    try:
        return await save_upload(storage, upload, settings.MAX_UPLOAD_BYTES)
    except UploadTooLarge:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.MAX_UPLOAD_BYTES}-byte limit",
        )


async def attach_case_blob(
    session: AsyncSession,
    *,
    sha256: str,
    size: int,
    content_type: str,
    case_id: int,
    owner_task_id: int | None = None,
    owner_log_id: int | None = None,
    name: str,
    organisation_id: str,
    created_by: str,
) -> AttachmentLink:
    """Attach to a case (or one of its tasks/logs). The link draws a per-case
    attachment id; the owner is the case itself unless owner_task_id/owner_log_id
    are given."""
    blob = await attachment_crud.get_or_create_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        created_by=created_by,
    )
    return await attachment_crud.create_link(
        session,
        attachment_id=blob.id,
        case_id=case_id,
        owner_task_id=owner_task_id,
        owner_log_id=owner_log_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )


async def attach_observable_blob(
    session: AsyncSession,
    *,
    sha256: str,
    size: int,
    content_type: str,
    observable_id: uuid.UUID,
    name: str,
    organisation_id: str,
    created_by: str,
) -> ObservableAttachmentLink:
    """Attach to an observable (UUID-identified; observables span cases)."""
    blob = await attachment_crud.get_or_create_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        created_by=created_by,
    )
    return await attachment_crud.create_observable_link(
        session,
        attachment_id=blob.id,
        observable_id=observable_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )


def stream_blob(
    storage: BlobStorage,
    link: AttachmentLink | ObservableAttachmentLink,
    blob: Attachment,
) -> StreamingResponse:
    return StreamingResponse(
        storage.stream(blob.sha256),
        media_type=blob.content_type,
        headers={"Content-Disposition": f'attachment; filename="{link.name}"'},
    )
