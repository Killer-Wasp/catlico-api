from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.sla import SlaPolicy, SlaPolicyUpsert


async def list_policies(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[SlaPolicy], int]:
    base = select(SlaPolicy).where(SlaPolicy.organisation_id == organisation_id)
    return await paginate(session, base, SlaPolicy.severity, skip=skip, limit=limit)


async def upsert_policy(
    session: AsyncSession,
    policy_in: SlaPolicyUpsert,
    *,
    organisation_id: str,
    created_by: str,
) -> SlaPolicy:
    result = await session.execute(
        select(SlaPolicy).where(
            SlaPolicy.organisation_id == organisation_id,
            SlaPolicy.severity == policy_in.severity,
        )
    )
    policy = result.scalar_one_or_none()
    if policy:
        policy.ack_seconds = policy_in.ack_seconds
        policy.resolve_seconds = policy_in.resolve_seconds
        policy.escalation_target = policy_in.escalation_target
        policy.enabled = policy_in.enabled
        policy.updated_by = created_by
    else:
        policy = SlaPolicy(
            organisation_id=organisation_id,
            severity=policy_in.severity,
            ack_seconds=policy_in.ack_seconds,
            resolve_seconds=policy_in.resolve_seconds,
            escalation_target=policy_in.escalation_target,
            enabled=policy_in.enabled,
            created_by=created_by,
        )
    session.add(policy)
    await session.flush()
    return policy


async def delete_policy(
    session: AsyncSession, policy: SlaPolicy
) -> None:
    await session.delete(policy)
    await session.flush()
