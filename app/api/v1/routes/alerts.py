import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ActiveOrgOrApiKeyContext
from app.api.v1.routes._files import (
    assert_attachment_type,
    attach_observable_blob,
    ingest_upload,
)
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import alert as alert_crud
from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import case_share as case_share_crud
from app.crud import case_template as ct_crud
from app.crud import comment as comment_crud
from app.crud import custom_field as cf_crud
from app.crud import enrichment as enrichment_crud
from app.crud import flag as flag_crud
from app.crud import observable as obs_crud
from app.crud import organisation_member as member_crud
from app.crud import role as role_crud
from app.crud import tag as tag_crud
from app.crud import user as user_crud
from app.models.alert import (
    ALERT_STATUS_TRANSITIONS,
    Alert,
    AlertBulkMerge,
    AlertCreate,
    AlertFacets,
    AlertPromote,
    AlertPublic,
    AlertStatus,
    AlertUpdate,
)
from app.models.case_ import CaseCreate, CasePublic, CaseStatus, SimilarCasePublic
from app.models.comment import (
    CommentCreate,
    CommentEntityType,
    CommentPublic,
    _display_name_from_email,
)
from app.models.common import Page
from app.models.custom_field import CustomFieldEntityType, CustomFieldValuesSet
from app.models.flag import FlagEntityType
from app.models.observable import ObservableCreate, ObservablePublic
from app.models.tag import TaggableType, TagSetRequest

router = APIRouter(prefix="/alerts", tags=["alerts"])

_OWNER_ROLE_NAME = "org-admin"


def _alert_public(
    alert: Alert, flagged: bool, custom_fields: dict[str, Any] | None = None
) -> AlertPublic:
    pub = AlertPublic.model_validate(alert, from_attributes=True)
    pub.flagged = flagged
    pub.custom_fields = custom_fields or {}
    return pub


def _require_perm(ctx: ActiveOrgOrApiKeyContext, permission: str) -> None:
    if permission not in ctx.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


async def _resolve_owned_alert(
    session: AsyncSession, ctx: ActiveOrgOrApiKeyContext, alert_id: int
) -> Alert:
    """Return the alert iff the active org owns it (or caller is superadmin).
    Alerts are org-owned with no sharing — visibility is direct ownership."""
    alert = await alert_crud.get_alert(session, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    if not ctx.user.is_superadmin and alert.organisation_id != ctx.organisation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return alert


@router.get("/", response_model=Page[AlertPublic])
async def list_alerts(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    status_filter: AlertStatus | None = None,
    type_filter: str | None = None,
    source_filter: str | None = None,
    severity: int | None = None,
    filter: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "id",
    order: Annotated[str, Query()] = "desc",
) -> Page[AlertPublic]:
    """Each `filter` term is `key~op~value` (keys: severity, source, tlp, alert,
    title, and `tag:<group-key>`), OR-within-key / AND-across-key. The legacy
    scalar params (status_filter/type_filter/…) still apply, AND-ed with clauses."""
    _require_perm(ctx, "read:alert")
    filters = alert_crud.AlertListFilter.from_query(filter, sort=sort, order=order)
    alerts, total = await alert_crud.list_alerts_for_org(
        session,
        ctx.organisation_id,
        skip=skip,
        limit=limit,
        status_filter=status_filter.value if status_filter else None,
        type_filter=type_filter,
        source_filter=source_filter,
        severity=severity,
        filters=filters,
    )
    flagged = await flag_crud.flagged_ids(
        session, FlagEntityType.alert, [str(a.id) for a in alerts], ctx.organisation_id
    )
    cfs = await cf_crud.values_for_entities(
        session, CustomFieldEntityType.alert, [str(a.id) for a in alerts]
    )
    return Page(
        items=[
            _alert_public(a, str(a.id) in flagged, cfs.get(str(a.id), {}))
            for a in alerts
        ],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/filters", response_model=AlertFacets)
async def list_alert_filters(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertFacets:
    """Distinct source + tag-key values across the org's alerts, for the list
    view's filter dropdowns. Declared before `/{alert_id}` so the literal wins."""
    _require_perm(ctx, "read:alert")
    return await alert_crud.alert_facets(session, ctx.organisation_id)


@router.post("/", response_model=AlertPublic)
async def ingest_alert(
    alert_in: AlertCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,  # set 200 (updated via dedup) vs 201 (created)
) -> AlertPublic:
    _require_perm(ctx, "write:alert")
    alert, created = await alert_crud.ingest_alert(
        session,
        alert_in,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.alert, str(alert.id), ctx.organisation_id
    )
    cfs = await cf_crud.values_for(session, CustomFieldEntityType.alert, str(alert.id))
    return _alert_public(alert, flagged, cfs)


@router.get("/{alert_id}", response_model=AlertPublic)
async def get_alert(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertPublic:
    _require_perm(ctx, "read:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.alert, str(alert.id), ctx.organisation_id
    )
    cfs = await cf_crud.values_for(session, CustomFieldEntityType.alert, str(alert.id))
    return _alert_public(alert, flagged, cfs)


@router.patch("/{alert_id}", response_model=AlertPublic)
async def update_alert(
    alert_id: int,
    alert_in: AlertUpdate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AlertPublic:
    _require_perm(ctx, "write:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)

    # Enforce the status state machine. `Imported` is reachable only via promotion.
    if alert_in.status is not None and alert_in.status != alert.status:
        if alert_in.status == AlertStatus.imported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Set status to Imported by promoting the alert, not directly",
            )
        allowed = ALERT_STATUS_TRANSITIONS.get(alert.status, set())
        if alert_in.status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Illegal status transition {alert.status.value} -> "
                    f"{alert_in.status.value}"
                ),
            )

    if alert_in.assignee_id:
        if not await member_crud.get_member(
            session, alert_in.assignee_id, alert.organisation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assignee must be a member of the alert's organisation",
            )

    alert = await alert_crud.update_alert(
        session, alert, alert_in, updated_by=str(ctx.user.id)
    )
    flagged = await flag_crud.is_flagged(
        session, FlagEntityType.alert, str(alert.id), ctx.organisation_id
    )
    cfs = await cf_crud.values_for(session, CustomFieldEntityType.alert, str(alert.id))
    return _alert_public(alert, flagged, cfs)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "write:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    await alert_crud.delete_alert(session, alert, deleted_by=str(ctx.user.id))


@router.post(
    "/{alert_id}/promote",
    response_model=CasePublic,
    status_code=status.HTTP_201_CREATED,
)
async def promote_alert(
    alert_id: int,
    promote_in: AlertPromote,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    # Consuming an alert and producing a case exercises both capabilities.
    _require_perm(ctx, "write:alert")
    _require_perm(ctx, "write:case")
    alert = await _resolve_owned_alert(session, ctx, alert_id)

    if alert.case_id is not None or alert.status == AlertStatus.imported:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert already promoted to case {alert.case_id}",
        )

    assignee_id = promote_in.assignee_id
    if assignee_id and not await member_crud.get_member(
        session, assignee_id, alert.organisation_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Assignee must be a member of the owner organisation",
        )

    owner_role = await role_crud.get_role_by_name(session, _OWNER_ROLE_NAME)
    if not owner_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Owner role '{_OWNER_ROLE_NAME}' not found",
        )

    # Optional case template: alert values take precedence; the template supplies the
    # title prefix, task scaffolding and tags.
    template = None
    if promote_in.case_template_id is not None:
        template = await ct_crud.get_template(
            session, promote_in.case_template_id, alert.organisation_id
        )
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Case template not found in this organisation",
            )

    base_title = promote_in.title or alert.title
    title = f"{template.title_prefix}{base_title}" if template else base_title
    case_in = CaseCreate(
        title=title,
        description=alert.description,
        severity=alert.severity,
        tlp=alert.tlp,
        pap=alert.pap,
        assignee_id=assignee_id,
        start_date=alert.date,  # anchor the case timeline to when the event happened
    )
    case = await case_crud.create_case(
        session,
        case_in,
        owner_org_id=alert.organisation_id,
        owner_role_id=owner_role.id,
        created_by=str(ctx.user.id),
    )
    if template is not None:
        await ct_crud.scaffold_tasks_into_case(
            session,
            template_id=template.id,
            case_id=case.id,
            organisation_id=alert.organisation_id,
            created_by=str(ctx.user.id),
        )
        tpl_tags = await tag_crud.list_tag_strings_for(
            session, TaggableType.case_template, str(template.id)
        )
        if tpl_tags:
            await tag_crud.set_tags(session, TaggableType.case, str(case.id), tpl_tags)
    await _import_alert_into_case(session, alert=alert, case_id=case.id, actor=str(ctx.user.id))
    return CasePublic.model_validate(case, from_attributes=True)


async def _import_alert_into_case(
    session: AsyncSession, *, alert: Alert, case_id: int, actor: str
) -> int:
    """Import one alert into a case: observables (deduped by type+value), tags
    (unioned), custom fields (added only where the case has none), then mark the
    alert Imported. Returns the observable count imported. Shared by single
    promote and bulk merge so both stay in lockstep."""
    imported = await obs_crud.import_alert_observables_to_case(
        session,
        alert_id=alert.id,
        case_id=case_id,
        organisation_id=alert.organisation_id,
        created_by=actor,
    )
    alert_tags = await tag_crud.list_tag_strings_for(
        session, TaggableType.alert, str(alert.id)
    )
    if alert_tags:
        existing = await tag_crud.list_tag_strings_for(
            session, TaggableType.case, str(case_id)
        )
        await tag_crud.set_tags(
            session, TaggableType.case, str(case_id), list({*existing, *alert_tags})
        )
    alert_cfs = await cf_crud.values_for(
        session, CustomFieldEntityType.alert, str(alert.id)
    )
    if alert_cfs:
        existing_cfs = await cf_crud.values_for(
            session, CustomFieldEntityType.case, str(case_id)
        )
        new_cfs = {k: v for k, v in alert_cfs.items() if k not in existing_cfs}
        if new_cfs:
            await cf_crud.set_values(
                session,
                CustomFieldEntityType.case,
                str(case_id),
                alert.organisation_id,
                new_cfs,
            )
    await alert_crud.mark_promoted(session, alert, case_id=case_id, updated_by=actor)
    return imported


@router.post("/merge", response_model=CasePublic, status_code=status.HTTP_201_CREATED)
async def merge_alerts(
    req: AlertBulkMerge,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    """Merge 1+ alerts into one case — a new case, or an existing one via
    `target_case_id`. All alerts must be owned by the acting org and not already
    promoted. See docs/case-merge-design.md."""
    _require_perm(ctx, "write:alert")
    _require_perm(ctx, "write:case")
    actor = str(ctx.user.id)

    alert_ids = sorted(set(req.alert_ids))
    if not alert_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one alert is required",
        )
    alerts = [await _resolve_owned_alert(session, ctx, aid) for aid in alert_ids]
    for alert in alerts:
        if alert.case_id is not None or alert.status == AlertStatus.imported:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Alert {alert.id} is already promoted to case {alert.case_id}",
            )

    max_tlp = max(a.tlp for a in alerts)
    max_pap = max(a.pap for a in alerts)
    raised: dict[str, list[int]] = {}

    if req.target_case_id is not None:
        case = await case_crud.get_case(session, req.target_case_id)
        owner_org = (
            None
            if case is None
            else next(
                (
                    s.organisation_id
                    for s in await case_share_crud.list_shares(session, case.id)
                    if s.is_owner
                ),
                None,
            )
        )
        if case is None or (
            not ctx.user.is_superadmin and owner_org != ctx.organisation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Target case not found"
            )
        if case.status == CaseStatus.duplicated:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Target case is merged (duplicated) and read-only",
            )
        # Never let an alert's data lose protection: raise the case to the most
        # restrictive TLP/PAP among the merged alerts.
        if case.tlp < max_tlp:
            raised["tlp"] = [case.tlp, max_tlp]
            case.tlp = max_tlp
        if case.pap < max_pap:
            raised["pap"] = [case.pap, max_pap]
            case.pap = max_pap
        if raised:
            case.updated_by = actor
            session.add(case)
            await session.flush()
    else:
        if req.assignee_id and not await member_crud.get_member(
            session, req.assignee_id, ctx.organisation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Assignee must be a member of the owner organisation",
            )
        owner_role = await role_crud.get_role_by_name(session, _OWNER_ROLE_NAME)
        if not owner_role:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Owner role '{_OWNER_ROLE_NAME}' not found",
            )
        template = None
        if req.case_template_id is not None:
            template = await ct_crud.get_template(
                session, req.case_template_id, ctx.organisation_id
            )
            if template is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Case template not found in this organisation",
                )
        base_title = req.title or alerts[0].title
        title = f"{template.title_prefix}{base_title}" if template else base_title
        description = "\n\n".join(a.description for a in alerts if a.description)
        case_in = CaseCreate(
            title=title,
            description=description,
            severity=max(a.severity for a in alerts),
            tlp=max_tlp,
            pap=max_pap,
            assignee_id=req.assignee_id,
            start_date=min(a.date for a in alerts),
        )
        case = await case_crud.create_case(
            session,
            case_in,
            owner_org_id=ctx.organisation_id,
            owner_role_id=owner_role.id,
            created_by=actor,
        )
        if template is not None:
            await ct_crud.scaffold_tasks_into_case(
                session,
                template_id=template.id,
                case_id=case.id,
                organisation_id=ctx.organisation_id,
                created_by=actor,
            )
            tpl_tags = await tag_crud.list_tag_strings_for(
                session, TaggableType.case_template, str(template.id)
            )
            if tpl_tags:
                await tag_crud.set_tags(
                    session, TaggableType.case, str(case.id), tpl_tags
                )

    total_obs = 0
    for alert in alerts:
        total_obs += await _import_alert_into_case(
            session, alert=alert, case_id=case.id, actor=actor
        )

    await audit_crud.record_audit(
        session,
        action="merge",
        obj=case,
        context=case,
        actor=actor,
        details={
            "alerts": alert_ids,
            "moved": {"observables": total_obs},
            "tlp_pap_raised": raised,
        },
    )
    return CasePublic.model_validate(case, from_attributes=True)


# --- Per-org flag ---

@router.put("/{alert_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def flag_alert(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "read:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    await flag_crud.set_flag(
        session,
        FlagEntityType.alert,
        str(alert.id),
        ctx.organisation_id,
        created_by=str(ctx.user.id),
    )


@router.delete("/{alert_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def unflag_alert(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    _require_perm(ctx, "read:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    await flag_crud.unset_flag(
        session, FlagEntityType.alert, str(alert.id), ctx.organisation_id
    )


# --- Observables on an alert ---

@router.get("/{alert_id}/observables", response_model=Page[ObservablePublic])
async def list_alert_observables(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[ObservablePublic]:
    _require_perm(ctx, "read:observable")
    await _resolve_owned_alert(session, ctx, alert_id)
    obs, total = await obs_crud.list_observables_for_alert(
        session, alert_id, skip=skip, limit=limit
    )
    return Page(items=obs, total=total, skip=skip, limit=limit)


@router.post(
    "/{alert_id}/observables",
    response_model=ObservablePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_alert_observable(
    alert_id: int,
    obs_in: ObservableCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservablePublic:
    _require_perm(ctx, "write:observable")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    err = await obs_crud.check_creatable_type(session, obs_in.observable_type)
    if err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
    observable = await obs_crud.create_alert_observable(
        session,
        obs_in,
        alert_id=alert.id,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    # Auto-enqueue enrichment for matching auto-run connectors
    await enrichment_crud.enqueue_auto_for_observable(
        session,
        observable,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return observable


@router.post(
    "/{alert_id}/observables/file",
    response_model=ObservablePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_alert_file_observable(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
    observable_type: Annotated[str, Form()] = "file",
    message: Annotated[str, Form()] = "",
    tlp: Annotated[int, Form()] = 2,
    ioc: Annotated[bool, Form()] = False,
    sighted: Annotated[bool, Form()] = False,
) -> ObservablePublic:
    _require_perm(ctx, "write:observable")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    await assert_attachment_type(session, observable_type)
    sha256, size, content_type = await ingest_upload(storage, file)
    observable = await obs_crud.create_alert_observable(
        session,
        ObservableCreate(
            observable_type=observable_type,
            data=sha256,
            message=message,
            tlp=tlp,
            ioc=ioc,
            sighted=sighted,
        ),
        alert_id=alert.id,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    await attach_observable_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        observable_id=observable.id,
        name=file.filename or sha256,
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    return observable


@router.get("/{alert_id}/similar-cases", response_model=list[SimilarCasePublic])
async def list_alert_similar_cases(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 20,
) -> list[SimilarCasePublic]:
    """Cases sharing one or more observables with this alert (the alert already
    belongs to, if promoted, is excluded)."""
    _require_perm(ctx, "read:case")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    rows = await obs_crud.similar_cases_for_alert(
        session,
        alert_id,
        organisation_id=ctx.organisation_id,
        exclude_case_id=alert.case_id,
        limit=limit,
    )
    return [
        SimilarCasePublic(
            id=case.id,
            title=case.title,
            severity=case.severity,
            status=case.status,
            shared_observables=shared,
        )
        for case, shared in rows
    ]


# --- Comments on an alert ---

def _comment_public(comment, author_name: str) -> CommentPublic:
    return CommentPublic(
        id=comment.id,
        entity_type=comment.entity_type,
        entity_id=comment.entity_id,
        message=comment.message,
        organisation_id=comment.organisation_id,
        created_at=comment.created_at,
        created_by=comment.created_by,
        updated_at=comment.updated_at,
        author_name=author_name,
    )


@router.get("/{alert_id}/comments", response_model=Page[CommentPublic])
async def list_alert_comments(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    sort_order: str = "desc",
) -> Page[CommentPublic]:
    _require_perm(ctx, "read:alert")
    await _resolve_owned_alert(session, ctx, alert_id)
    comments, total = await comment_crud.list_comments(
        session,
        CommentEntityType.alert,
        str(alert_id),
        skip=skip,
        limit=limit,
        sort_order=sort_order,
    )
    emails = await user_crud.emails_for_ids(
        session, [uuid.UUID(c.created_by) for c in comments]
    )
    items = [
        _comment_public(
            c,
            _display_name_from_email(emails.get(uuid.UUID(c.created_by), "")),
        )
        for c in comments
    ]
    return Page(items=items, total=total, skip=skip, limit=limit)


@router.post(
    "/{alert_id}/comments",
    response_model=CommentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_alert_comment(
    alert_id: int,
    comment_in: CommentCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentPublic:
    _require_perm(ctx, "write:alert")
    await _resolve_owned_alert(session, ctx, alert_id)
    comment = await comment_crud.create_comment(
        session,
        comment_in,
        entity_type=CommentEntityType.alert,
        entity_id=str(alert_id),
        organisation_id=ctx.organisation_id,
        created_by=str(ctx.user.id),
    )
    emails = await user_crud.emails_for_ids(session, [ctx.user.id])
    author_name = _display_name_from_email(emails.get(ctx.user.id, ""))
    return _comment_public(comment, author_name)


# --- Tags on an alert ---

@router.get("/{alert_id}/tags", response_model=list[str])
async def list_alert_tags(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    _require_perm(ctx, "read:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    return await tag_crud.list_tag_strings_for(session, TaggableType.alert, str(alert.id))


@router.put("/{alert_id}/tags", response_model=list[str])
async def set_alert_tags(
    alert_id: int,
    body: TagSetRequest,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    _require_perm(ctx, "write:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    await tag_crud.set_tags(session, TaggableType.alert, str(alert.id), body.tags)
    return await tag_crud.list_tag_strings_for(session, TaggableType.alert, str(alert.id))


# --- Custom fields on an alert ---

@router.get("/{alert_id}/custom-fields", response_model=dict[str, Any])
async def get_alert_custom_fields(
    alert_id: int,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    _require_perm(ctx, "read:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    return await cf_crud.values_for(session, CustomFieldEntityType.alert, str(alert.id))


@router.put("/{alert_id}/custom-fields", response_model=dict[str, Any])
async def set_alert_custom_fields(
    alert_id: int,
    body: CustomFieldValuesSet,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    _require_perm(ctx, "write:alert")
    alert = await _resolve_owned_alert(session, ctx, alert_id)
    try:
        return await cf_crud.set_values(
            session,
            CustomFieldEntityType.alert,
            str(alert.id),
            ctx.organisation_id,
            body.values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
