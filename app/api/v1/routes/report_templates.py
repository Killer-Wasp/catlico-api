"""G4: Report templates CRUD and routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import (
    ActiveOrgContext,
    CaseAuthContext,
    _resolve_case_context,
    require_case_permission,
)
from app.core.db import get_session
from app.models.report_template import (
    ReportTemplate, ReportTemplateCreate, ReportTemplateUpdate, ReportTemplatePublic,
)

router = APIRouter(prefix="/report-templates", tags=["report-templates"])


def _ensure_admin(ctx: ActiveOrgContext):
    if "write:organisation" not in ctx.permissions and not ctx.user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org admin required")


@router.get("", response_model=list[ReportTemplatePublic])
async def list_templates(ctx: ActiveOrgContext, db=Depends(get_session)):
    result = await db.execute(
        select(ReportTemplate).where(ReportTemplate.organisation_id == ctx.organisation_id).order_by(ReportTemplate.name)
    )
    return [ReportTemplatePublic(**t.__dict__) for t in result.scalars().all()]


@router.post("", response_model=ReportTemplatePublic, status_code=201)
async def create_template(body: ReportTemplateCreate, ctx: ActiveOrgContext, db=Depends(get_session)):
    _ensure_admin(ctx)
    t = ReportTemplate(organisation_id=ctx.organisation_id, name=body.name, description=body.description,
                       content_md=body.content_md, config=body.config, created_by=str(ctx.user.id))
    db.add(t); await db.flush(); return ReportTemplatePublic(**t.__dict__)


@router.patch("/{template_id}", response_model=ReportTemplatePublic)
async def update_template(template_id: uuid.UUID, body: ReportTemplateUpdate, ctx: ActiveOrgContext, db=Depends(get_session)):
    _ensure_admin(ctx)
    t = await db.get(ReportTemplate, template_id)
    if not t or t.organisation_id != ctx.organisation_id: raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items(): setattr(t, k, v)
    t.updated_by = str(ctx.user.id); db.add(t); await db.flush(); return ReportTemplatePublic(**t.__dict__)


@router.delete("/{template_id}", status_code=204)
async def delete_template(template_id: uuid.UUID, ctx: ActiveOrgContext, db=Depends(get_session)):
    _ensure_admin(ctx)
    t = await db.get(ReportTemplate, template_id)
    if not t or t.organisation_id != ctx.organisation_id: raise HTTPException(404, "Not found")
    await db.delete(t); await db.flush()


# --- Case report rendering ---


@router.get("/{template_id}/render")
async def render_case_report(
    template_id: uuid.UUID,
    ctx: ActiveOrgContext,
    db: AsyncSession = Depends(get_session),
    case_id: int = Query(...),
    fmt: str = Query("html"),
):
    """Render a report template for a specific case. Format: html or markdown."""
    t = await db.get(ReportTemplate, template_id)
    if not t or t.organisation_id != ctx.organisation_id:
        raise HTTPException(404, "Template not found")
    # Verify case visibility before rendering
    await _resolve_case_context(case_id, ctx, db)
    from app.services.case_reporting import render_report
    try:
        content = await render_report(db, t, case_id, fmt=fmt)
        if fmt == "html":
            from fastapi.responses import HTMLResponse
            return HTMLResponse(content=content)
        return {"format": fmt, "content": content}
    except ValueError as e:
        raise HTTPException(404, str(e))
