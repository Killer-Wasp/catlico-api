import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.audit import record_audit
from app.crud.pagination import paginate
from app.models.comment import Comment, CommentCreate, CommentEntityType, CommentUpdate


def _comment_context(comment: Comment) -> tuple[str | None, str | None]:
    """A comment's activity-feed context is the entity it's attached to."""
    return comment.entity_type.value, comment.entity_id


async def get_comment(session: AsyncSession, comment_id: uuid.UUID) -> Comment | None:
    comment = await session.get(Comment, comment_id)
    if comment is None or comment.deleted_at is not None:
        return None
    return comment


async def list_comments(
    session: AsyncSession,
    entity_type: CommentEntityType,
    entity_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Comment], int]:
    base = select(Comment).where(
        Comment.entity_type == entity_type,
        Comment.entity_id == entity_id,
        Comment.deleted_at.is_(None),
    )
    return await paginate(session, base, Comment.created_at, skip=skip, limit=limit)


async def create_comment(
    session: AsyncSession,
    comment_in: CommentCreate,
    *,
    entity_type: CommentEntityType,
    entity_id: str,
    organisation_id: str,
    created_by: str,
) -> Comment:
    comment = Comment(
        entity_type=entity_type,
        entity_id=entity_id,
        message=comment_in.message,
        organisation_id=organisation_id,
        created_by=created_by,
    )
    session.add(comment)
    await session.flush()
    ctx_type, ctx_id = _comment_context(comment)
    await record_audit(
        session,
        action="create",
        obj=comment,
        context_type=ctx_type,
        context_id=ctx_id,
        actor=created_by,
    )
    return comment


async def update_comment(
    session: AsyncSession, comment: Comment, comment_in: CommentUpdate, updated_by: str
) -> Comment:
    comment.message = comment_in.message
    comment.updated_at = datetime.now(UTC)
    comment.updated_by = updated_by
    session.add(comment)
    await session.flush()
    ctx_type, ctx_id = _comment_context(comment)
    await record_audit(
        session,
        action="update",
        obj=comment,
        context_type=ctx_type,
        context_id=ctx_id,
        actor=updated_by,
    )
    return comment


async def delete_comment(session: AsyncSession, comment: Comment, deleted_by: str) -> None:
    comment.deleted_at = datetime.now(UTC)
    comment.deleted_by = deleted_by
    session.add(comment)
    await session.flush()
    ctx_type, ctx_id = _comment_context(comment)
    await record_audit(
        session,
        action="delete",
        obj=comment,
        context_type=ctx_type,
        context_id=ctx_id,
        actor=deleted_by,
    )
