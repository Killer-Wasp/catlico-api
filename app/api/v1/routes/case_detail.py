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
from app.crud import assignee as assignee_crud
from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import flag as flag_crud
from app.models.case_ import CasePublic, CaseUpdate
from app.models.common import AssigneeSetRequest
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
        owner_org_id = await _case_owner_org_id(session, case_ctx.case.id)
        await assert_assignee_in_org(session, update_data["assignee_id"], owner_org_id)

    if "status_id" in update_data and update_data["status_id"] is not None:
        from app.crud import case_status as case_status_crud

        owner_org_id = await _case_owner_org_id(session, case_ctx.case.id)
        if await case_status_crud.get_status(
            session, update_data["status_id"], owner_org_id
        ) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Status not found in this organisation",
            )

    case = await case_crud.update_case(
        session,
        case_ctx.case,
        case_in,
        updated_by=str(case_ctx.user.id),
        organisation_id=case_ctx.organisation_id,
    )
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.case, str(case.id), case_ctx.organisation_id
    )
    cfs = await custom_fields_for_case(session, case.id)
    return await case_public_resolved(
        case, session, flagged, cfs, organisation_id=case_ctx.organisation_id
    )


async def _case_owner_org_id(session: AsyncSession, case_id: int) -> str:
    """The owner org of a case (mirrors the PATCH assignee-validation path)."""
    from app.crud.case_share import list_shares

    shares = await list_shares(session, case_id)
    owner_org_id = next((s.organisation_id for s in shares if s.is_owner), None)
    if owner_org_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Case has no owner organisation",
        )
    return owner_org_id


@router.put("/assignees", response_model=CasePublic)
async def set_case_assignees(
    body: AssigneeSetRequest,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    """Replace the case's collaborator (secondary-assignee) set. The primary owner
    is set separately via PATCH `assignee_id`. Each collaborator must be a member
    of the owner organisation (mirrors the PATCH assignee check). Newly-added
    collaborators are stamped into the audit event for targeted `case.assigned`
    notification fan-out."""
    case = case_ctx.case
    if body.user_ids:
        owner_org_id = await _case_owner_org_id(session, case.id)
        for uid in set(body.user_ids):
            await assert_assignee_in_org(session, uid, owner_org_id)

    before = set(await assignee_crud.list_case_collaborators(session, case.id))
    added = await assignee_crud.set_case_collaborators(
        session, case.id, body.user_ids, primary_id=case.assignee_id
    )
    after = set(await assignee_crud.list_case_collaborators(session, case.id))
    if before != after:
        await audit_crud.record_audit(
            session,
            action="update",
            obj=case,
            context=case,
            actor=str(case_ctx.user.id),
            details={
                "added_assignee_ids": [str(u) for u in added],
                "assignee_ids": sorted(str(u) for u in after),
            },
            organisation_id=case_ctx.organisation_id,
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
    await case_crud.delete_case(
        session,
        case_ctx.case,
        deleted_by=str(case_ctx.user.id),
        organisation_id=case_ctx.organisation_id,
    )
