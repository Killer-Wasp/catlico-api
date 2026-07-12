"""F1: MITRE ATT&CK pattern and procedure routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext, CaseAuthContext, require_case_permission
from app.core.db import get_session
from app.crud import pattern as pattern_crud
from app.models.common import Page
from app.models.pattern import PatternImportItem, PatternPublic, ProcedurePublic, ProcedureReplace

router = APIRouter(tags=["patterns"])


# --- Patterns ---

pattern_router = APIRouter(prefix="/patterns")


@pattern_router.get("", response_model=Page[PatternPublic])
async def list_patterns(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[PatternPublic]:
    patterns, total = await pattern_crud.list_patterns(session, skip=skip, limit=limit)
    return Page(
        items=[PatternPublic.model_validate(p, from_attributes=True) for p in patterns],
        total=total,
        skip=skip,
        limit=limit,
    )


@pattern_router.post("/import", response_model=list[PatternPublic])
async def import_patterns(
    items: list[PatternImportItem],
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[PatternPublic]:
    """Bulk import/upsert ATT&CK patterns by external_id.

    Patterns are a global catalog shared across organisations, so writes are an
    admin surface: ``write:organisation``, matching the planned
    ``POST /patterns/import-attack`` guard (attack-matrix design doc)."""
    if "write:organisation" not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing permission: write:organisation",
        )
    patterns = await pattern_crud.import_patterns(
        session, items, created_by=str(ctx.user.id)
    )
    return [PatternPublic.model_validate(p, from_attributes=True) for p in patterns]


# --- Procedures (case-scoped) ---

procedures_router = APIRouter(prefix="/cases")


@procedures_router.get("/{case_id}/procedures", response_model=list[ProcedurePublic])
async def list_case_procedures(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProcedurePublic]:
    procedures = await pattern_crud.list_procedures(session, case_ctx.case.id)
    out: list[ProcedurePublic] = []
    for proc in procedures:
        pattern = None
        # ponytail: inline pattern load; eager-load if N+1 becomes real
        from app.models.pattern import Pattern
        p = await session.get(Pattern, proc.pattern_id)
        if p:
            pattern = PatternPublic.model_validate(p, from_attributes=True)
        out.append(
            ProcedurePublic(
                id=proc.id,
                case_id=proc.case_id,
                pattern_id=proc.pattern_id,
                pattern=pattern,
                description=proc.description,
                created_at=proc.created_at,
            )
        )
    return out


@procedures_router.put("/{case_id}/procedures", response_model=list[ProcedurePublic])
async def replace_case_procedures(
    body: ProcedureReplace,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ProcedurePublic]:
    procedures = await pattern_crud.replace_procedures(
        session, case_ctx.case.id, body, created_by=str(case_ctx.user.id)
    )
    out: list[ProcedurePublic] = []
    for proc in procedures:
        from app.models.pattern import Pattern
        p = await session.get(Pattern, proc.pattern_id)
        pattern = PatternPublic.model_validate(p, from_attributes=True) if p else None
        out.append(
            ProcedurePublic(
                id=proc.id,
                case_id=proc.case_id,
                pattern_id=proc.pattern_id,
                pattern=pattern,
                description=proc.description,
                created_at=proc.created_at,
            )
        )
    return out


router.include_router(pattern_router)
router.include_router(procedures_router)
