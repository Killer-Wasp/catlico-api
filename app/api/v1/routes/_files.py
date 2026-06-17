"""Shared helpers for file uploads/downloads across observables and task logs."""
from fastapi import HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.core.storage import BlobStorage, UploadTooLarge, save_upload
from app.crud import attachment as attachment_crud
from app.crud import observable as obs_crud
from app.models.attachment import Attachment, AttachmentLink, AttachmentOwnerType


async def assert_attachment_type(session: AsyncSession, type_name: str) -> None:
    """File endpoints require a registered attachment-backed type (e.g. 'file')."""
    t = await obs_crud.get_type(session, type_name)
    if t is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown observable type: {type_name}",
        )
    if not t.is_attachment:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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


async def attach_blob(
    session: AsyncSession,
    *,
    sha256: str,
    size: int,
    content_type: str,
    owner_type: AttachmentOwnerType,
    owner_id: str,
    name: str,
    organisation_id: str,
    created_by: str,
) -> AttachmentLink:
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
        owner_type=owner_type,
        owner_id=owner_id,
        name=name,
        organisation_id=organisation_id,
        created_by=created_by,
    )


def stream_blob(
    storage: BlobStorage, link: AttachmentLink, blob: Attachment
) -> StreamingResponse:
    return StreamingResponse(
        storage.stream(blob.sha256),
        media_type=blob.content_type,
        headers={"Content-Disposition": f'attachment; filename="{link.name}"'},
    )
