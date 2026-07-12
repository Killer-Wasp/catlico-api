from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.crud.pagination import paginate
from app.models.sla import SlaPolicy, SlaPolicyUpsert

# Fraction of the resolve target that must elapse before an open case is flagged
# "at-risk" (rather than "ok"). 0.8 == the last 20% of the window.
_AT_RISK_FRACTION = 0.8


async def resolve_targets(session: AsyncSession, organisation_id: str) -> dict[int, int]:
    """severity → resolve-SLA seconds, for the org's ENABLED policies only.

    Mirrors the overview KPI computation so per-case chips and the org-wide
    breach count agree on what counts."""
    rows = (
        await session.execute(
            select(SlaPolicy.severity, SlaPolicy.resolve_seconds).where(
                SlaPolicy.organisation_id == organisation_id,
                SlaPolicy.enabled.is_(True),
            )
        )
    ).all()
    return {sev: secs for sev, secs in rows}


def compute_case_sla(
    *,
    severity: int,
    created_at: datetime | None,
    is_open: bool,
    resolve_targets: dict[int, int],
    now: datetime,
) -> tuple[datetime | None, str | None]:
    """Return (sla_due_at, sla_state) for one case.

    `sla_due_at` is `created_at + resolve_seconds` whenever an enabled policy
    exists for the severity. `sla_state` ("ok" | "at-risk" | "breached") is only
    meaningful for an OPEN case — a resolved case has no live countdown, so its
    state is None. `now` and `created_at` must share tz-awareness (callers pass
    naive UTC, matching how the DB stores/returns timestamps here)."""
    target = resolve_targets.get(severity)
    if target is None or created_at is None:
        return None, None
    due_at = created_at + timedelta(seconds=target)
    if not is_open:
        return due_at, None
    if now >= due_at:
        return due_at, "breached"
    if now >= created_at + timedelta(seconds=target * _AT_RISK_FRACTION):
        return due_at, "at-risk"
    return due_at, "ok"


async def list_policies(
    session: AsyncSession,
    organisation_id: str,
    *,
    skip: int = 0,
    limit: int = 100,
) -> tuple[list[SlaPolicy], int]:
    base = select(SlaPolicy).where(SlaPolicy.organisation_id == organisation_id)
    return await paginate(session, base, SlaPolicy.severity, skip=skip, limit=limit)


async def get_policy(
    session: AsyncSession, policy_id: int, organisation_id: str
) -> SlaPolicy | None:
    result = await session.execute(
        select(SlaPolicy).where(
            SlaPolicy.id == policy_id,
            SlaPolicy.organisation_id == organisation_id,
        )
    )
    return result.scalar_one_or_none()


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
