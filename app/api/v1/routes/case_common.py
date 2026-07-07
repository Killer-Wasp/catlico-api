import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.crud import custom_field as cf_crud
from app.crud import organisation_member as member_crud
from app.crud import tag as tag_crud
from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.case_ import Case, CasePublic, CaseTaskSummary
from app.models.custom_field import CustomFieldEntityType
from app.models.tag import TaggableType

OWNER_ROLE_NAME = "org-admin"


def case_public(
    case: Case,
    flagged: bool,
    custom_fields: dict[str, Any] | None = None,
    lineage: tuple[int | None, list[int]] | None = None,
) -> CasePublic:
    pub = CasePublic.model_validate(case, from_attributes=True)
    pub.flagged = flagged
    pub.custom_fields = custom_fields or {}
    if lineage is not None:
        pub.merged_into, pub.merged_from = lineage
    return pub


async def case_public_resolved(
    case: Case,
    session: AsyncSession,
    flagged: bool,
    custom_fields: dict[str, Any] | None = None,
    lineage: tuple[int | None, list[int]] | None = None,
) -> CasePublic:
    """Single-case projection that resolves assignee_email, tags, and task summaries."""
    pub = case_public(case, flagged, custom_fields, lineage)
    if case.assignee_id:
        emails = await user_crud.emails_for_ids(session, [case.assignee_id])
        pub.assignee_email = emails.get(case.assignee_id)
    pub.tags = await tag_crud.list_tag_strings_for(
        session, TaggableType.case, str(case.id)
    )
    tasks_map = await task_crud.summaries_for_cases(session, [case.id])
    pub.tasks = [
        CaseTaskSummary(id=t.id, public_id=t.public_id, title=t.title, status=t.status)
        for t in tasks_map.get(case.id, [])
    ]
    return pub


def require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def assert_assignee_in_org(
    session: AsyncSession, assignee_id: uuid.UUID, organisation_id: str
) -> None:
    member = await member_crud.get_member(session, assignee_id, organisation_id)
    if not member:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Assignee must be a member of the owner organisation",
        )


async def custom_fields_for_case(session: AsyncSession, case_id: int) -> dict[str, Any]:
    return await cf_crud.values_for(session, CustomFieldEntityType.case, str(case_id))
