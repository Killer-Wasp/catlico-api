from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgContext
from app.core.db import get_session
from app.crud import notification as notif_crud
from app.models.common import Page
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierPublic,
    NotifierUpdate,
    NotificationRuleCreate,
    NotificationRulePublic,
    NotificationRuleUpdate,
    UserNotification,
    UserNotificationPublic,
    UserNotificationUpdate,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _ensure_org_admin(ctx: ActiveOrgContext) -> None:
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )


# --- Notifiers ---

notifier_router = APIRouter(prefix="/notifiers", tags=["notifiers"])


def _notifier_public(notifier: Notifier) -> NotifierPublic:
    return NotifierPublic(
        id=notifier.id,
        type=notifier.type,
        target=notifier.target,
        config=notifier.config,
        enabled=notifier.enabled,
        has_secrets=notifier.secrets_encrypted is not None,
        organisation_id=notifier.organisation_id,
        created_at=notifier.created_at,
        updated_at=notifier.updated_at,
    )


@notifier_router.get("/", response_model=Page[NotifierPublic])
async def list_notifiers(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[NotifierPublic]:
    _ensure_org_admin(ctx)
    notifiers, total = await notif_crud.list_notifiers(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[_notifier_public(n) for n in notifiers],
        total=total,
        skip=skip,
        limit=limit,
    )


@notifier_router.post("/", response_model=NotifierPublic, status_code=status.HTTP_201_CREATED)
async def create_notifier(
    notifier_in: NotifierCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotifierPublic:
    _ensure_org_admin(ctx)
    notifier = await notif_crud.create_notifier(
        session,
        notifier_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return _notifier_public(notifier)


@notifier_router.patch("/{notifier_id}", response_model=NotifierPublic)
async def update_notifier(
    notifier_id: UUID,
    notifier_in: NotifierUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotifierPublic:
    _ensure_org_admin(ctx)
    notifier = await notif_crud.get_notifier(session, notifier_id, ctx.organisation_id)
    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notifier not found"
        )
    notifier = await notif_crud.update_notifier(
        session, notifier, notifier_in, updated_by=str(ctx.user.id)
    )
    return _notifier_public(notifier)


@notifier_router.delete("/{notifier_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notifier(
    notifier_id: UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _ensure_org_admin(ctx)
    notifier = await notif_crud.get_notifier(session, notifier_id, ctx.organisation_id)
    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notifier not found"
        )
    await notif_crud.delete_notifier(session, notifier)


# --- Notification Rules ---

rule_router = APIRouter(prefix="/notification-rules", tags=["notification-rules"])


@rule_router.get("/", response_model=Page[NotificationRulePublic])
async def list_rules(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[NotificationRulePublic]:
    _ensure_org_admin(ctx)
    rules, total = await notif_crud.list_rules(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[NotificationRulePublic.model_validate(r, from_attributes=True) for r in rules],
        total=total,
        skip=skip,
        limit=limit,
    )


@rule_router.post("/", response_model=NotificationRulePublic, status_code=status.HTTP_201_CREATED)
async def create_rule(
    rule_in: NotificationRuleCreate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationRulePublic:
    _ensure_org_admin(ctx)
    rule = await notif_crud.create_rule(
        session,
        rule_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return NotificationRulePublic.model_validate(rule, from_attributes=True)


@rule_router.patch("/{rule_id}", response_model=NotificationRulePublic)
async def update_rule(
    rule_id: UUID,
    rule_in: NotificationRuleUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationRulePublic:
    _ensure_org_admin(ctx)
    rule = await notif_crud.get_rule(session, rule_id, ctx.organisation_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification rule not found"
        )
    rule = await notif_crud.update_rule(
        session, rule, rule_in, updated_by=str(ctx.user.id)
    )
    return NotificationRulePublic.model_validate(rule, from_attributes=True)


@rule_router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: UUID,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _ensure_org_admin(ctx)
    rule = await notif_crud.get_rule(session, rule_id, ctx.organisation_id)
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification rule not found"
        )
    await notif_crud.delete_rule(session, rule)


# --- User Notification Feed (A2) ---


feed_router = APIRouter(prefix="", tags=["notifications"])


def _notification_public(n: UserNotification) -> UserNotificationPublic:
    return UserNotificationPublic(
        id=n.id,
        event_type=n.event_type,
        title=n.title,
        body=n.body,
        payload=n.payload,
        read_at=n.read_at,
        created_at=n.created_at,
    )


@feed_router.get("/", response_model=Page[UserNotificationPublic])
async def list_feed(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    unread: bool = False,
) -> Page[UserNotificationPublic]:
    items, total = await notif_crud.list_user_notifications(
        session,
        ctx.organisation_id,
        ctx.user.id,
        skip=skip,
        limit=limit,
        unread_only=unread,
    )
    return Page(
        items=[_notification_public(n) for n in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@feed_router.patch("/{notification_id}", response_model=UserNotificationPublic)
async def update_feed_item(
    notification_id: UUID,
    notif_in: UserNotificationUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserNotificationPublic:
    notif = await notif_crud.get_user_notification(
        session, notification_id, ctx.organisation_id
    )
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Cross-user visibility: only the target user (or org-wide) can mark read
    if notif.user_id is not None and notif.user_id != ctx.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your notification")
    notif = await notif_crud.update_user_notification(session, notif, notif_in)
    return _notification_public(notif)


@feed_router.post("/read-all", status_code=status.HTTP_200_OK)
async def read_all(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    count = await notif_crud.mark_all_read(
        session, ctx.organisation_id, ctx.user.id
    )
    return {"marked_read": count}


router.include_router(notifier_router)
router.include_router(rule_router)
router.include_router(feed_router)
