import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.crypto import encrypt_secrets
from app.crud.pagination import paginate
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierUpdate,
    NotificationRule,
    NotificationRuleCreate,
    NotificationRuleUpdate,
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
