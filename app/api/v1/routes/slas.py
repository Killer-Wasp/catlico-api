from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import sla as sla_crud
from app.models.common import Page
from app.models.sla import SlaPolicyPublic, SlaPolicyUpsert

router = APIRouter(prefix="/sla-policies", tags=["sla-policies"])


def _ensure_org_admin(ctx: ActiveOrgOrApiKeyContext) -> None:
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )


@router.get("/", response_model=Page[SlaPolicyPublic])
async def list_sla_policies(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[SlaPolicyPublic]:
    if "read:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organisation admin required"
        )
    policies, total = await sla_crud.list_policies(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[SlaPolicyPublic.model_validate(p, from_attributes=True) for p in policies],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.put("/", response_model=list[SlaPolicyPublic])
async def upsert_sla_policies(
    policies_in: list[SlaPolicyUpsert],
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SlaPolicyPublic]:
    _ensure_org_admin(ctx)
    policies = []
    for p_in in policies_in:
        p = await sla_crud.upsert_policy(
            session,
            p_in,
            organisation_id=ctx.organisation_id,
            created_by=str(ctx.user.id),
        )
        policies.append(SlaPolicyPublic.model_validate(p, from_attributes=True))
    return policies


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sla_policy(
    policy_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _ensure_org_admin(ctx)
    policy = await sla_crud.get_policy(session, policy_id, ctx.organisation_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="SLA policy not found"
        )
    await sla_crud.delete_policy(session, policy)
