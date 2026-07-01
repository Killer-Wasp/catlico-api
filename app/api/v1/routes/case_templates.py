from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import case_template as ct_crud
from app.crud import tag as tag_crud
from app.models.case_template import (
    CaseTemplate,
    CaseTemplateCreate,
    CaseTemplateExport,
    CaseTemplatePublic,
    CaseTemplateTaskIn,
    CaseTemplateTaskPublic,
    CaseTemplateUpdate,
)
from app.models.common import Page
from app.models.tag import TaggableType, TagSetRequest

router = APIRouter(prefix="/case-templates", tags=["case-templates"])


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def _to_public(
    session: AsyncSession, tpl: CaseTemplate
) -> CaseTemplatePublic:
    tasks = await ct_crud.list_template_tasks(session, tpl.id)
    tags = await tag_crud.list_tag_strings_for(
        session, TaggableType.case_template, str(tpl.id)
    )
    pub = CaseTemplatePublic.model_validate(tpl, from_attributes=True)
    pub.tasks = [CaseTemplateTaskPublic.model_validate(t, from_attributes=True) for t in tasks]
    pub.tags = tags
    return pub


async def _resolve(
    session: AsyncSession, ctx: ActiveOrgOrApiKeyContext, template_id: int
) -> CaseTemplate:
    tpl = await ct_crud.get_template(session, template_id, ctx.organisation_id)
    if tpl is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Case template not found"
        )
    return tpl


@router.get("/", response_model=Page[CaseTemplatePublic])
async def list_case_templates(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[CaseTemplatePublic]:
    _require_perm(ctx, "read:case")
    tpls, total = await ct_crud.list_templates(
        session, ctx.organisation_id, skip=skip, limit=limit
    )
    return Page(
        items=[await _to_public(session, t) for t in tpls],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=CaseTemplatePublic, status_code=status.HTTP_201_CREATED)
async def create_case_template(
    tpl_in: CaseTemplateCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseTemplatePublic:
    _require_perm(ctx, "write:case")
    tpl = await ct_crud.create_template(
        session, tpl_in, organisation_id=ctx.organisation_id, created_by=str(ctx.user.id)
    )
    return await _to_public(session, tpl)


@router.post(
    "/import", response_model=CaseTemplatePublic, status_code=status.HTTP_201_CREATED
)
async def import_case_template(
    doc: CaseTemplateExport,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseTemplatePublic:
    """Create a template in the caller's org from a portable export document."""
    _require_perm(ctx, "write:case")
    if doc.kind != "catlico.caseTemplate":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported document kind: {doc.kind}",
        )
    if await ct_crud.get_template_by_name(session, doc.name, ctx.organisation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A template named '{doc.name}' already exists in this organisation; "
                "rename it in the document to import a copy"
            ),
        )
    tpl = await ct_crud.create_template(
        session,
        CaseTemplateCreate(
            name=doc.name,
            display_name=doc.display_name,
            title_prefix=doc.title_prefix,
            description=doc.description,
            severity=doc.severity,
            tlp=doc.tlp,
            pap=doc.pap,
            summary=doc.summary,
            tasks=[CaseTemplateTaskIn.model_validate(t, from_attributes=True) for t in doc.tasks],
        ),
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    if doc.tags:
        await tag_crud.set_tags(session, TaggableType.case_template, str(tpl.id), doc.tags)
    return await _to_public(session, tpl)


@router.get("/{template_id}/export", response_model=CaseTemplateExport)
async def export_case_template(
    template_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseTemplateExport:
    """Portable JSON for sharing a playbook across orgs/instances."""
    _require_perm(ctx, "read:case")
    tpl = await _resolve(session, ctx, template_id)
    tasks = await ct_crud.list_template_tasks(session, tpl.id)
    tags = await tag_crud.list_tag_strings_for(
        session, TaggableType.case_template, str(tpl.id)
    )
    return CaseTemplateExport(
        name=tpl.name,
        display_name=tpl.display_name,
        title_prefix=tpl.title_prefix,
        description=tpl.description,
        severity=tpl.severity,
        tlp=tpl.tlp,
        pap=tpl.pap,
        summary=tpl.summary,
        tasks=[CaseTemplateTaskIn.model_validate(t, from_attributes=True) for t in tasks],
        tags=tags,
    )


@router.get("/{template_id}", response_model=CaseTemplatePublic)
async def get_case_template(
    template_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseTemplatePublic:
    _require_perm(ctx, "read:case")
    tpl = await _resolve(session, ctx, template_id)
    return await _to_public(session, tpl)


@router.patch("/{template_id}", response_model=CaseTemplatePublic)
async def update_case_template(
    template_id: int,
    tpl_in: CaseTemplateUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseTemplatePublic:
    _require_perm(ctx, "write:case")
    tpl = await _resolve(session, ctx, template_id)
    tpl = await ct_crud.update_template(session, tpl, tpl_in, updated_by=str(ctx.user.id))
    return await _to_public(session, tpl)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case_template(
    template_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "write:case")
    tpl = await _resolve(session, ctx, template_id)
    await ct_crud.delete_template(session, tpl, deleted_by=str(ctx.user.id))


@router.get("/{template_id}/tags", response_model=list[str])
async def list_template_tags(
    template_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    _require_perm(ctx, "read:case")
    tpl = await _resolve(session, ctx, template_id)
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.case_template, str(tpl.id)
    )


@router.put("/{template_id}/tags", response_model=list[str])
async def set_template_tags(
    template_id: int,
    body: TagSetRequest,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    _require_perm(ctx, "write:case")
    tpl = await _resolve(session, ctx, template_id)
    await tag_crud.set_tags(
        session, TaggableType.case_template, str(tpl.id), body.tags
    )
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.case_template, str(tpl.id)
    )
