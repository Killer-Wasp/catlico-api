import uuid
from datetime import UTC, datetime

from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.crypto import encrypt_secrets
from app.crud.pagination import paginate
from app.models.common import utcnow
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierUpdate,
    NotificationRule,
    NotificationRuleCreate,
    NotificationRuleUpdate,
    UserNotification,
    UserNotificationPreference,
    UserNotificationUpdate,
)


# --- Notifier ---

async def get_notifier(
    session: AsyncSession, notifier_id: uuid.UUID, organisation_id: str
) -> Notifier | None:
    result = await session.execute(
        select(Notifier).where(
            Notifier.id == notifier_id,
            Notifier.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def list_notifiers(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[Notifier], int]:
    base = select(Notifier).where(Notifier.organisation_id == organisation_id)
    return await paginate(session, base, Notifier.created_at.desc(), skip=skip, limit=limit)


async def create_notifier(
    session: AsyncSession,
    notifier_in: NotifierCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> Notifier:
    notifier = Notifier(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        type=notifier_in.type,
        target=notifier_in.target,
        config=notifier_in.config,
        secrets_encrypted=encrypt_secrets(notifier_in.secrets),
        enabled=notifier_in.enabled,
        created_by=created_by,
    )
    session.add(notifier)
    await session.flush()
    return notifier


async def update_notifier(
    session: AsyncSession,
    notifier: Notifier,
    notifier_in: NotifierUpdate,
    updated_by: str,
) -> Notifier:
    update_data = notifier_in.model_dump(exclude_unset=True)
    # ponytail: encrypt secrets on update; settings-only updates preserve existing encrypted blob
    if "secrets" in update_data:
        update_data["secrets_encrypted"] = encrypt_secrets(update_data.pop("secrets"))
    for k, v in update_data.items():
        setattr(notifier, k, v)
    notifier.updated_by = updated_by
    session.add(notifier)
    await session.flush()
    return notifier


async def delete_notifier(
    session: AsyncSession, notifier: Notifier
) -> None:
    await session.delete(notifier)
    await session.flush()


# --- NotificationRule ---

async def get_rule(
    session: AsyncSession, rule_id: uuid.UUID, organisation_id: str
) -> NotificationRule | None:
    result = await session.execute(
        select(NotificationRule).where(
            NotificationRule.id == rule_id,
            NotificationRule.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def list_rules(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[NotificationRule], int]:
    base = select(NotificationRule).where(NotificationRule.organisation_id == organisation_id)
    return await paginate(session, base, NotificationRule.created_at.desc(), skip=skip, limit=limit)


async def create_rule(
    session: AsyncSession,
    rule_in: NotificationRuleCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> NotificationRule:
    rule = NotificationRule(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        name=rule_in.name,
        description=rule_in.description,
        event=rule_in.event,
        enabled=rule_in.enabled,
        notifier_ids=rule_in.notifier_ids,
        created_by=created_by,
    )
    session.add(rule)
    await session.flush()
    return rule


async def update_rule(
    session: AsyncSession,
    rule: NotificationRule,
    rule_in: NotificationRuleUpdate,
    updated_by: str,
) -> NotificationRule:
    update_data = rule_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(rule, k, v)
    rule.updated_by = updated_by
    session.add(rule)
    await session.flush()
    return rule


async def delete_rule(
    session: AsyncSession, rule: NotificationRule
) -> None:
    await session.delete(rule)
    await session.flush()


# --- UserNotification (A2) ---


async def create_notification(
    session: AsyncSession,
    *,
    organisation_id: str,
    user_id: uuid.UUID | None,
    event_type: str,
    title: str,
    body: str = "",
    payload: dict | None = None,
) -> UserNotification:
    notif = UserNotification(
        organisation_id=organisation_id,
        user_id=user_id,
        event_type=event_type,
        title=title,
        body=body,
        payload=payload or {},
    )
    session.add(notif)
    await session.flush()
    return notif


async def list_user_notifications(
    session: AsyncSession,
    organisation_id: str,
    user_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
    unread_only: bool = False,
) -> tuple[list[UserNotification], int]:
    """Visible rows: same org AND (null user_id OR matching user_id)."""
    base = select(UserNotification).where(
        UserNotification.organisation_id == organisation_id,
        or_(
            UserNotification.user_id.is_(None),
            UserNotification.user_id == user_id,
        ),
    )
    if unread_only:
        base = base.where(UserNotification.read_at.is_(None))
    disabled = await disabled_event_types(session, organisation_id, user_id)
    if disabled:
        base = base.where(UserNotification.event_type.notin_(disabled))
    return await paginate(
        session, base, UserNotification.created_at.desc(), skip=skip, limit=limit
    )


async def get_user_notification(
    session: AsyncSession, notification_id: uuid.UUID, organisation_id: str
) -> UserNotification | None:
    result = await session.execute(
        select(UserNotification).where(
            UserNotification.id == notification_id,
            UserNotification.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


async def update_user_notification(
    session: AsyncSession,
    notif: UserNotification,
    notif_in: UserNotificationUpdate,
) -> UserNotification:
    update_data = notif_in.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(notif, k, v)
    session.add(notif)
    await session.flush()
    return notif


async def mark_all_read(
    session: AsyncSession, organisation_id: str, user_id: uuid.UUID
) -> int:
    """Mark all visible unread notifications as read. Returns count updated."""
    result = await session.execute(
        select(UserNotification).where(
            UserNotification.organisation_id == organisation_id,
            or_(
                UserNotification.user_id.is_(None),
                UserNotification.user_id == user_id,
            ),
            UserNotification.read_at.is_(None),
        )
    )
    rows = result.scalars().all()
    disabled = await disabled_event_types(session, organisation_id, user_id)
    now = datetime.now(UTC)
    marked = 0
    for r in rows:
        if r.event_type in disabled:
            continue
        r.read_at = now
        session.add(r)
        marked += 1
    await session.flush()
    return marked


# --- UserNotificationPreference (A2) ---


async def get_user_preferences(
    session: AsyncSession, organisation_id: str, user_id: uuid.UUID
) -> dict[str, bool]:
    """Stored preference rows only: `{event_type: enabled}`."""
    result = await session.execute(
        select(UserNotificationPreference).where(
            UserNotificationPreference.organisation_id == organisation_id,
            UserNotificationPreference.user_id == user_id,
        )
    )
    return {p.event_type: p.enabled for p in result.scalars().all()}


async def disabled_event_types(
    session: AsyncSession, organisation_id: str, user_id: uuid.UUID
) -> set[str]:
    """Event types the user has explicitly disabled (`enabled is False`)."""
    result = await session.execute(
        select(UserNotificationPreference.event_type).where(
            UserNotificationPreference.organisation_id == organisation_id,
            UserNotificationPreference.user_id == user_id,
            UserNotificationPreference.enabled.is_(False),
        )
    )
    return set(result.scalars().all())


async def set_user_preferences(
    session: AsyncSession,
    organisation_id: str,
    user_id: uuid.UUID,
    prefs: dict[str, bool],
    actor: str,
) -> None:
    """Upsert each `(user, org, event_type)` preference row in one atomic
    statement. Concurrent PUTs can't race into a `uq_user_notif_pref` violation
    because the conflict is resolved by the DB via `ON CONFLICT DO UPDATE`."""
    if not prefs:
        return
    now = utcnow()
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "organisation_id": organisation_id,
            "event_type": event_type,
            "enabled": enabled,
            "created_at": now,
            "created_by": actor,
        }
        for event_type, enabled in prefs.items()
    ]
    stmt = pg_insert(UserNotificationPreference).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "organisation_id", "event_type"],
        set_={
            "enabled": stmt.excluded.enabled,
            "updated_by": actor,
            "updated_at": now,
        },
    )
    await session.execute(stmt)
    await session.flush()
