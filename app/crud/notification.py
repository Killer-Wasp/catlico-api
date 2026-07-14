import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, literal as sa_literal, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.crypto import decrypt_secrets, encrypt_secrets
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
    UserNotificationRead,
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


def _merge_secrets(existing_blob: str | None, incoming: dict) -> str | None:
    """Apply the secret-write contract onto the stored set (mirrors plugin config
    secrets): absent key keeps, string replaces, ``None`` deletes. Merging rather
    than overwriting means a single-field update can't silently drop the others."""
    merged = decrypt_secrets(existing_blob)
    for key, value in incoming.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return encrypt_secrets(merged)


async def create_notifier(
    session: AsyncSession,
    notifier_in: NotifierCreate,
    *,
    organisation_id: str,
    created_by: str,
) -> Notifier:
    from app.services.net_guard import derive_label

    secrets = dict(notifier_in.secrets)
    # `target` is a display label; if the client didn't supply one, derive it from
    # the (secret) destination URL so it never carries the secret path tail.
    target = notifier_in.target
    url = secrets.get("url")
    if not target and url:
        target = derive_label(url)
    notifier = Notifier(
        id=uuid.uuid4(),
        organisation_id=organisation_id,
        type=notifier_in.type,
        target=target,
        config=notifier_in.config,
        secrets_encrypted=encrypt_secrets(secrets),
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
    from app.services.net_guard import derive_label

    update_data = notifier_in.model_dump(exclude_unset=True)
    if "secrets" in update_data:
        incoming = update_data.pop("secrets") or {}
        update_data["secrets_encrypted"] = _merge_secrets(
            notifier.secrets_encrypted, incoming
        )
        # Re-derive the display label when the destination URL changes, unless the
        # caller explicitly set a target of their own in the same request.
        if "url" in incoming and incoming["url"] and "target" not in update_data:
            update_data["target"] = derive_label(incoming["url"])
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
    outbox_id: int | None = None,
) -> UserNotification | None:
    """Insert a feed notification. When `outbox_id` is set the insert is
    idempotent per (outbox event[, user]) via the partial unique indexes —
    a drain retry returns None instead of duplicating the row."""
    stmt = (
        pg_insert(UserNotification)
        .values(
            id=uuid.uuid4(),
            organisation_id=organisation_id,
            user_id=user_id,
            outbox_id=outbox_id,
            event_type=event_type,
            title=title,
            body=body,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
        .returning(UserNotification)
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.scalars().one_or_none()


async def list_user_notifications(
    session: AsyncSession,
    organisation_id: str,
    user_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 100,
    unread_only: bool = False,
) -> tuple[list[UserNotification], int, dict[uuid.UUID, datetime]]:
    """Visible rows: same org AND (null user_id OR matching user_id). Returns
    (rows, total, read_receipts) where read_receipts maps notification id →
    this user's read_at."""
    read_exists = (
        select(UserNotificationRead.id)
        .where(
            UserNotificationRead.notification_id == UserNotification.id,
            UserNotificationRead.user_id == user_id,
        )
        .exists()
    )
    base = select(UserNotification).where(
        UserNotification.organisation_id == organisation_id,
        or_(
            UserNotification.user_id.is_(None),
            UserNotification.user_id == user_id,
        ),
    )
    if unread_only:
        base = base.where(~read_exists)
    disabled = await disabled_event_types(session, organisation_id, user_id)
    if disabled:
        base = base.where(UserNotification.event_type.notin_(disabled))
    rows, total = await paginate(
        session, base, UserNotification.created_at.desc(), skip=skip, limit=limit
    )
    receipts: dict[uuid.UUID, datetime] = {}
    ids = [n.id for n in rows]
    if ids:
        result = await session.execute(
            select(
                UserNotificationRead.notification_id, UserNotificationRead.read_at
            ).where(
                UserNotificationRead.user_id == user_id,
                UserNotificationRead.notification_id.in_(ids),
            )
        )
        receipts = {nid: read_at for nid, read_at in result.all()}
    return rows, total, receipts


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


async def set_read_state(
    session: AsyncSession,
    notification_id: uuid.UUID,
    user_id: uuid.UUID,
    read_at: datetime | None,
) -> None:
    """Upsert (read) or delete (unread) this user's receipt for one notification."""
    if read_at is None:
        await session.execute(
            delete(UserNotificationRead).where(
                UserNotificationRead.notification_id == notification_id,
                UserNotificationRead.user_id == user_id,
            )
        )
    else:
        stmt = pg_insert(UserNotificationRead).values(
            id=uuid.uuid4(),
            notification_id=notification_id,
            user_id=user_id,
            read_at=read_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["notification_id", "user_id"],
            set_={"read_at": read_at},
        )
        await session.execute(stmt)
    await session.flush()


async def mark_all_read(
    session: AsyncSession, organisation_id: str, user_id: uuid.UUID
) -> int:
    """Insert receipts for every visible, unmuted, unread notification.
    Returns the number of notifications newly marked read."""
    disabled = await disabled_event_types(session, organisation_id, user_id)
    read_exists = (
        select(UserNotificationRead.id)
        .where(
            UserNotificationRead.notification_id == UserNotification.id,
            UserNotificationRead.user_id == user_id,
        )
        .exists()
    )
    now = datetime.now(UTC)
    sel = select(
        func.gen_random_uuid(),
        UserNotification.id,
        sa_literal(user_id),
        sa_literal(now),
    ).where(
        UserNotification.organisation_id == organisation_id,
        or_(
            UserNotification.user_id.is_(None),
            UserNotification.user_id == user_id,
        ),
        ~read_exists,
    )
    if disabled:
        sel = sel.where(UserNotification.event_type.notin_(disabled))
    stmt = pg_insert(UserNotificationRead).from_select(
        ["id", "notification_id", "user_id", "read_at"], sel
    ).on_conflict_do_nothing(index_elements=["notification_id", "user_id"])
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount


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
