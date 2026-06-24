import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import comment as comment_crud
from app.crud import user as user_crud
from app.crud.case_share import get_share, list_shares
from app.models.comment import Comment, CommentEntityType, CommentPublic, CommentUpdate, _display_name_from_email

router = APIRouter(prefix="/comments", tags=["comments"])


async def _resolve_comment(
    session: AsyncSession, ctx: ActiveOrgContext, comment_id: uuid.UUID
) -> Comment:
    """Comments ride the parent's visibility. Today the only parent is a case, so the
    active org must be able to see that case."""
    comment = await comment_crud.get_comment(session, comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if ctx.user.is_superadmin:
        return comment
    if comment.entity_type == CommentEntityType.case:
        share = await get_share(session, int(comment.entity_id), ctx.organisation_id)
        if share is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found"
            )
    return comment


@router.patch("/{comment_id}", response_model=CommentPublic)
async def update_comment(
    comment_id: uuid.UUID,
    comment_in: CommentUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentPublic:
    comment = await _resolve_comment(session, ctx, comment_id)
    # Author-only edit.
    if comment.created_by != str(ctx.user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author can edit this comment",
        )
    comment = await comment_crud.update_comment(
        session, comment, comment_in, updated_by=str(ctx.user.id)
    )
    emails = await user_crud.emails_for_ids(session, [uuid.UUID(comment.created_by)])
    author_name = _display_name_from_email(
        emails.get(uuid.UUID(comment.created_by), "")
    )
    return CommentPublic(
        id=comment.id,
        entity_type=comment.entity_type,
        entity_id=comment.entity_id,
        message=comment.message,
        organisation_id=comment.organisation_id,
        created_at=comment.created_at,
        created_by=comment.created_by,
        updated_at=comment.updated_at,
        author_name=author_name,
    )


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    comment_id: uuid.UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    comment = await _resolve_comment(session, ctx, comment_id)
    # Author OR the case owner org may delete.
    is_author = comment.created_by == str(ctx.user.id)
    is_case_owner = False
    if comment.entity_type == CommentEntityType.case:
        shares = await list_shares(session, int(comment.entity_id))
        owner_org_id = next((s.organisation_id for s in shares if s.is_owner), None)
        is_case_owner = owner_org_id == ctx.organisation_id
    if not (is_author or is_case_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author or the case owner can delete this comment",
        )
    await comment_crud.delete_comment(session, comment, deleted_by=str(ctx.user.id))
