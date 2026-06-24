from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SuperAdminUser
from app.core.db import get_session
from app.crud import audit as audit_crud
from app.models.audit import AuditPublic
from app.models.common import Page

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/", response_model=Page[AuditPublic])
async def list_audit(
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    action: str | None = None,
    object_type: str | None = None,
    context_type: str | None = None,
    context_id: str | None = None,
) -> Page[AuditPublic]:
    rows, total = await audit_crud.list_audits(
        session,
        skip=skip,
        limit=limit,
        action=action,
        object_type=object_type,
        context_type=context_type,
        context_id=context_id,
    )
    return Page(items=rows, total=total, skip=skip, limit=limit)
