from datetime import datetime
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
    NotificationPreferenceItem,
    NotificationPreferencesPublic,
    NotificationPreferencesUpdate,
    UserNotification,
    UserNotificationPublic,
    UserNotificationUpdate,
)
from app.services.net_guard import SsrfError, validate_url_shallow
from app.services.notification_catalog import (
    NOTIFICATION_EVENT_CATALOG,
    catalog_event_types,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _ensure_org_admin(ctx: ActiveOrgContext) -> None:
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )


def _validate_secret_url(secrets: dict | None) -> None:
    """Early, DNS-free feedback on a destination URL at create/update time. The
    authoritative resolve-and-deny still runs at send time (DNS can change)."""
    if not secrets:
        return
    url = secrets.get("url")
    if not url:
        return
    try:
        validate_url_shallow(url)
    except SsrfError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


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
    _validate_secret_url(notifier_in.secrets)
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
    _validate_secret_url(notifier_in.secrets)
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


def _notification_public(
    n: UserNotification, read_at: datetime | None
) -> UserNotificationPublic:
    return UserNotificationPublic(
        id=n.id,
        event_type=n.event_type,
        title=n.title,
        body=n.body,
        payload=n.payload,
        read_at=read_at,
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
    items, total, receipts = await notif_crud.list_user_notifications(
        session,
        ctx.organisation_id,
        ctx.user.id,
        skip=skip,
        limit=limit,
        unread_only=unread,
    )
    return Page(
        items=[_notification_public(n, receipts.get(n.id)) for n in items],
        total=total,
        skip=skip,
        limit=limit,
    )


async def _merged_preferences(
    session: AsyncSession, organisation_id: str, user_id: UUID
) -> NotificationPreferencesPublic:
    stored = await notif_crud.get_user_preferences(session, organisation_id, user_id)
    items = [
        NotificationPreferenceItem(
            event_type=event_type,
            label=label,
            category=category,
            enabled=stored.get(event_type, True),
        )
        for event_type, label, category in NOTIFICATION_EVENT_CATALOG
    ]
    return NotificationPreferencesPublic(items=items)


@feed_router.get("/preferences", response_model=NotificationPreferencesPublic)
async def get_preferences(
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPreferencesPublic:
    return await _merged_preferences(session, ctx.organisation_id, ctx.user.id)


@feed_router.put("/preferences", response_model=NotificationPreferencesPublic)
async def update_preferences(
    prefs_in: NotificationPreferencesUpdate,
    ctx: ActiveOrgContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPreferencesPublic:
    valid = catalog_event_types()
    unknown = set(prefs_in.preferences) - valid
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown event type(s): {', '.join(sorted(unknown))}",
        )
    await notif_crud.set_user_preferences(
        session,
        ctx.organisation_id,
        ctx.user.id,
        prefs_in.preferences,
        actor=str(ctx.user.id),
    )
    return await _merged_preferences(session, ctx.organisation_id, ctx.user.id)


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
    # Targeted notifications are only actionable by their target user.
    if notif.user_id is not None and notif.user_id != ctx.user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your notification")
    await notif_crud.set_read_state(
        session, notif.id, ctx.user.id, notif_in.read_at
    )
    return _notification_public(notif, notif_in.read_at)


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
