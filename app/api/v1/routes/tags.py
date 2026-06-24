from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SuperAdminUser
from app.core.db import get_session
from app.crud import tag as tag_crud
from app.models.tag import Tag, TagCreate, TagPublic, TagUpdate, tag_to_string

router = APIRouter(prefix="/tags", tags=["tags"])


def _to_public(tag) -> TagPublic:
    return TagPublic(
        id=tag.id,
        namespace=tag.namespace,
        predicate=tag.predicate,
        value=tag.value,
        description=tag.description,
        colour=tag.colour,
        tag=tag_to_string(tag),
    )


@router.get("/", response_model=list[TagPublic])
async def list_tags(
    _: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    namespace: str | None = None,
) -> list[TagPublic]:
    tags = await tag_crud.list_all_tags(session, namespace=namespace)
    return [_to_public(t) for t in tags]


@router.post("/", response_model=TagPublic, status_code=status.HTTP_201_CREATED)
async def create_tag(
    admin: SuperAdminUser,
    tag_in: TagCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagPublic:
    try:
        tag = await tag_crud.create_tag(session, tag_in)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return _to_public(tag)


@router.patch("/{tag_id}", response_model=TagPublic)
async def update_tag(
    admin: SuperAdminUser,
    tag_id: int,
    tag_in: TagUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TagPublic:
    tag = await session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )
    tag = await tag_crud.update_tag(session, tag, tag_in)
    return _to_public(tag)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    admin: SuperAdminUser,
    tag_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    tag = await session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )
    await tag_crud.delete_tag(session, tag)
