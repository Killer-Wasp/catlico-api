from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CaseAuthContext,
    require_case_owner,
    require_case_permission,
)
from app.api.v1.routes.case_common import (
    assert_assignee_in_org,
    case_public_resolved,
    custom_fields_for_case,
)
from app.core.db import get_session
from app.crud import case_ as case_crud
from app.crud import flag as flag_crud
from app.models.case_ import CasePublic, CaseUpdate
from app.models.flag import FlagEntityType

router = APIRouter(prefix="/{case_id}", tags=["cases"])


@router.get("", response_model=CasePublic)
async def get_case(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.case, str(case_ctx.case.id), case_ctx.organisation_id
    )
    cfs = await custom_fields_for_case(session, case_ctx.case.id)
    lineage = await case_crud.lineage_for_many(session, [case_ctx.case.id])
    return await case_public_resolved(
        case_ctx.case,
        session,
        flagged,
        cfs,
        lineage.get(case_ctx.case.id),
        organisation_id=case_ctx.organisation_id,
    )


@router.patch("", response_model=CasePublic)
async def update_case(
    case_in: CaseUpdate,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    update_data = case_in.model_dump(exclude_unset=True)

    owner_only_fields = {"tlp", "pap"}
    if update_data.keys() & owner_only_fields and not case_ctx.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner organisation can change TLP/PAP",
        )

    if "assignee_id" in update_data and update_data["assignee_id"] is not None:
        from app.crud.case_share import list_shares

        shares = await list_shares(session, case_ctx.case.id)
        owner_org_id = next((s.organisation_id for s in shares if s.is_owner), None)
        if owner_org_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Case has no owner organisation",
            )
        await assert_assignee_in_org(session, update_data["assignee_id"], owner_org_id)

    case = await case_crud.update_case(
        session, case_ctx.case, case_in, updated_by=str(case_ctx.user.id)
    )
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.case, str(case.id), case_ctx.organisation_id
    )
    cfs = await custom_fields_for_case(session, case.id)
    return await case_public_resolved(
        case, session, flagged, cfs, organisation_id=case_ctx.organisation_id
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_ctx: Annotated[CaseAuthContext, require_case_owner("delete:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await case_crud.delete_case(session, case_ctx.case, deleted_by=str(case_ctx.user.id))
