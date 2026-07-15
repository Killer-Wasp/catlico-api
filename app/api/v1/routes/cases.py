import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ActiveOrgOrApiKeyContext,
    CaseAuthContext,
    require_case_owner,
    require_case_permission,
)
from app.api.v1.routes._files import (
    assert_attachment_type,
    attach_case_blob,
    attach_observable_blob,
    ingest_upload,
    stream_blob,
)
from app.api.v1.routes.case_common import (
    OWNER_ROLE_NAME,
    assert_assignee_in_org,
    case_public,
    case_public_resolved,
    require_perm,
)
from app.api.v1.routes.case_detail import router as case_detail_router
from app.api.v1.routes.plugins import ManualPluginRunRequest
from app.core.db import get_session
from app.core.storage import BlobStorage, get_storage
from app.crud import alert as alert_crud
from app.crud import assignee as assignee_crud
from app.crud import audit as audit_crud
from app.crud import attachment as attachment_crud
from app.crud import case_ as case_crud
from app.crud import comment as comment_crud
from app.crud import custom_field as cf_crud
from app.crud import flag as flag_crud
from app.crud import log as log_crud
from app.crud import case_template as ct_crud
from app.crud import observable as obs_crud
from app.crud import sla as sla_crud
from app.crud import role as role_crud
from app.crud import tag as tag_crud
from app.crud import task as task_crud
from app.crud import user as user_crud
from app.models.alert import AlertPublic
from app.models.attachment import AttachmentPublic
from app.models.case_ import (
    Case,
    CaseCounts,
    CaseCreate,
    CaseListFacets,
    CaseMergeRequest,
    CasePublic,
    CaseTaskSummary,
    SimilarCasePublic,
)
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
from app.models.task import Task, TaskCreate, TaskPublic

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("/", response_model=Page[CasePublic])
async def list_cases(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    filter: Annotated[list[str] | None, Query()] = None,
    sort: Annotated[str, Query()] = "id",
    order: Annotated[str, Query()] = "desc",
) -> Page[CasePublic]:
    """Filterable, sortable, paginated case list. Each `filter` term is
    `key~op~value` (op ∈ {eq, co}); terms are OR-within a key and AND-across keys
    — e.g. (status eq Open OR status eq Resolved) AND (title co "phish"). Keys:
    status, severity, assignee (email or the literal `Unassigned`), title, case,
    and `tag:<group-key>` for tag values. `sort` ∈ {id, created, updated},
    `order` ∈ {asc, desc}."""
    require_perm(ctx, "read:case")
    filters = case_crud.CaseListFilter.from_query(filter, sort=sort, order=order)
    cases, total = await case_crud.list_cases_for_org(
        session,
        ctx.organisation_id,
        skip=skip,
        limit=limit,
        filters=filters,
    )
    flagged = await flag_crud.flagged_ids(
        session, FlagEntityType.case, [str(c.id) for c in cases], ctx.organisation_id
    )
    cfs = await cf_crud.values_for_entities(
        session, CustomFieldEntityType.case, [str(c.id) for c in cases]
    )
    lineage = await case_crud.lineage_for_many(session, [c.id for c in cases])
    tags_map = await tag_crud.tags_for_many(
        session, TaggableType.case, [str(c.id) for c in cases]
    )
    collaborators_map = await assignee_crud.collaborators_for_cases(
        session, [c.id for c in cases]
    )
    # One id→email lookup covering primaries + collaborators across the page.
    all_assignee_ids = {c.assignee_id for c in cases if c.assignee_id}
    for collab in collaborators_map.values():
        all_assignee_ids.update(collab)
    emails = await user_crud.emails_for_ids(session, list(all_assignee_ids))
    tasks_map = await task_crud.summaries_for_cases(session, [c.id for c in cases])
    sla_targets = await sla_crud.resolve_targets(session, ctx.organisation_id)
    now = datetime.now(UTC)

    def _public(c: Case) -> CasePublic:
        pub = case_public(
            c,
            str(c.id) in flagged,
            cfs.get(str(c.id), {}),
            lineage.get(c.id),
            sla_targets=sla_targets,
            now=now,
        )
        pub.tags = tags_map.get(str(c.id), [])
        pub.assignee_email = emails.get(c.assignee_id) if c.assignee_id else None
        pub.assignees = assignee_crud.assignee_refs(
            c.assignee_id, collaborators_map.get(c.id, []), emails
        )
        pub.tasks = [
            CaseTaskSummary(
                id=t.id,
                public_id=t.public_id,
                title=t.title,
                status=t.status,
            )
            for t in tasks_map.get(c.id, [])
        ]
        return pub

    return Page(
        items=[_public(c) for c in cases],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/filters", response_model=CaseListFacets)
async def list_case_filters(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseListFacets:
    """Distinct assignee/tag values across the org's cases for the list view's
    filter dropdowns. Declared before `/{case_id}` so the literal path wins."""
    require_perm(ctx, "read:case")
    return await case_crud.case_list_facets(session, ctx.organisation_id)


@router.post("/", response_model=CasePublic, status_code=status.HTTP_201_CREATED)
async def create_case(
    case_in: CaseCreate,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    require_perm(ctx, "write:case")
    if case_in.assignee_id:
        await assert_assignee_in_org(session, case_in.assignee_id, ctx.organisation_id)
    owner_role = await role_crud.get_role_by_name(session, OWNER_ROLE_NAME, ctx.organisation_id)
    if not owner_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Owner role '{OWNER_ROLE_NAME}' not found",
        )

    template = None
    effective = case_in
    if case_in.case_template_id is not None:
        template = await ct_crud.get_template(
            session, case_in.case_template_id, ctx.organisation_id
        )
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Case template not found in this organisation",
            )
        # Explicit request fields win; the template fills the rest.
        set_fields = case_in.model_fields_set
        data = case_in.model_dump()
        for field in ("severity", "tlp", "pap"):
            if field not in set_fields and getattr(template, field) is not None:
                data[field] = getattr(template, field)
        if "description" not in set_fields and template.description:
            data["description"] = template.description
        if "summary" not in set_fields and template.summary is not None:
            data["summary"] = template.summary
        data["title"] = f"{template.title_prefix}{case_in.title}"
        effective = CaseCreate(**data)

    case = await case_crud.create_case(
        session,
        effective,
        owner_org_id=ctx.organisation_id,
        owner_role_id=owner_role.id,
        created_by=str(ctx.user.id),
    )

    if template is not None:
        await ct_crud.scaffold_tasks_into_case(
            session,
            template_id=template.id,
            case_id=case.id,
            organisation_id=ctx.organisation_id,
            created_by=str(ctx.user.id),
        )
        tpl_tags = await tag_crud.list_tag_strings_for(
            session, TaggableType.case_template, str(template.id)
        )
        if tpl_tags:
            await tag_crud.set_tags(session, TaggableType.case, str(case.id), tpl_tags)

    return await case_public_resolved(
        case, session, flagged=False, organisation_id=ctx.organisation_id
    )


@router.post("/merge", response_model=CasePublic, status_code=status.HTTP_201_CREATED)
async def merge_cases(
    req: CaseMergeRequest,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CasePublic:
    """Merge 2+ same-owner-org cases into a fresh case. Sources are frozen as
    Duplicated and read-only; children are reparented onto the new case. The acting
    org must hold write:case and own every source. See docs/case-merge-design.md."""
    require_perm(ctx, "write:case")
    if req.case.assignee_id:
        await assert_assignee_in_org(session, req.case.assignee_id, ctx.organisation_id)
    owner_role = await role_crud.get_role_by_name(session, OWNER_ROLE_NAME, ctx.organisation_id)
    if not owner_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Owner role '{OWNER_ROLE_NAME}' not found",
        )
    try:
        case = await case_crud.merge_cases(
            session,
            source_ids=req.source_ids,
            case_in=req.case,
            owner_org_id=ctx.organisation_id,
            owner_role_id=owner_role.id,
            actor=str(ctx.user.id),
        )
    except case_crud.MergeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    lineage = await case_crud.lineage_for_many(session, [case.id])
    return await case_public_resolved(
        case,
        session,
        flagged=False,
        lineage=lineage.get(case.id),
        organisation_id=ctx.organisation_id,
    )


router.include_router(case_detail_router)


# --- Per-org flag ---

@router.put("/{case_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def flag_case(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await flag_crud.set_flag(
        session,
        FlagEntityType.case,
        str(case_ctx.case.id),
        case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )


@router.delete("/{case_id}/flag", status_code=status.HTTP_204_NO_CONTENT)
async def unflag_case(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await flag_crud.unset_flag(
        session, FlagEntityType.case, str(case_ctx.case.id), case_ctx.organisation_id
    )


# --- Tasks under a case ---

def _task_public(task: Task, flagged: bool) -> TaskPublic:
    pub = TaskPublic.model_validate(task, from_attributes=True)
    pub.flagged = flagged
    return pub


@router.get("/{case_id}/tasks", response_model=Page[TaskPublic])
async def list_case_tasks(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:task")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[TaskPublic]:
    tasks, total = await task_crud.list_tasks_for_case(
        session,
        case_ctx.case.id,
        organisation_id=case_ctx.organisation_id,
        is_owner=case_ctx.is_owner,
        skip=skip,
        limit=limit,
    )
    flagged = await flag_crud.flagged_ids(
        session, FlagEntityType.task, [t.public_id for t in tasks], case_ctx.organisation_id
    )
    log_counts = await log_crud.log_counts_for_case(session, case_ctx.case.id)
    collaborators_map = await assignee_crud.collaborators_for_tasks(
        session, [(t.case_id, t.id) for t in tasks]
    )
    all_assignee_ids = {t.assignee_id for t in tasks if t.assignee_id}
    for collab in collaborators_map.values():
        all_assignee_ids.update(collab)
    emails = await user_crud.emails_for_ids(session, list(all_assignee_ids))

    def _public(task: Task) -> TaskPublic:
        pub = _task_public(task, task.public_id in flagged)
        pub.log_count = log_counts.get(task.id, 0)
        pub.assignees = assignee_crud.assignee_refs(
            task.assignee_id, collaborators_map.get((task.case_id, task.id), []), emails
        )
        return pub

    return Page(
        items=[_public(t) for t in tasks],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/{case_id}/tasks",
    response_model=TaskPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_task(
    task_in: TaskCreate,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:task")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskPublic:
    if task_in.assignee_id:
        await assert_assignee_in_org(
            session, task_in.assignee_id, case_ctx.organisation_id
        )
    task = await task_crud.create_task(
        session,
        task_in,
        case_id=case_ctx.case.id,
        organisation_id=case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )
    pub = _task_public(task, flagged=False)
    pub.assignees = await assignee_crud.build_assignee_refs(
        session, primary_id=task.assignee_id, collaborator_ids=[]
    )
    return pub


# --- Observables under a case ---

@router.get("/{case_id}/observables", response_model=Page[ObservablePublic])
async def list_case_observables(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:observable")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[ObservablePublic]:
    obs, total = await obs_crud.list_observables_for_case(
        session,
        case_ctx.case.id,
        organisation_id=case_ctx.organisation_id,
        is_owner=case_ctx.is_owner,
        skip=skip,
        limit=limit,
    )
    items = await obs_crud.to_public_list(session, obs)
    return Page(items=items, total=total, skip=skip, limit=limit)


@router.post(
    "/{case_id}/observables",
    response_model=ObservablePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_observable(
    obs_in: ObservableCreate,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:observable")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ObservablePublic:
    err = await obs_crud.check_creatable_type(session, obs_in.observable_type)
    if err:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=err)
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Observable with this type and value already exists on the case",
    )
    if await obs_crud.find_case_observable(
        session, case_ctx.case.id, obs_in.observable_type, obs_in.data
    ):
        raise conflict
    # The find/create above is a TOCTOU: a concurrent create of the same
    # (case, type, data) can insert the row between our pre-check and our insert,
    # and the partial unique index uq_observable_case_dedup then rejects the loser
    # with an IntegrityError. Run the insert in a savepoint so a collision rolls
    # back cleanly without poisoning the surrounding transaction (which would make
    # the re-check below fail with PendingRollbackError), then re-check.
    try:
        async with session.begin_nested():
            observable = await obs_crud.create_case_observable(
                session,
                obs_in,
                case_id=case_ctx.case.id,
                organisation_id=case_ctx.organisation_id,
                created_by=str(case_ctx.user.id),
            )
    except IntegrityError:
        # If the row now exists, a concurrent create won the race: resolve it as
        # the same 409 the non-racing duplicate path returns. If it still doesn't
        # exist, the violation was some other constraint — re-raise, never swallow.
        if await obs_crud.find_case_observable(
            session, case_ctx.case.id, obs_in.observable_type, obs_in.data
        ):
            raise conflict
        raise
    return await obs_crud.to_public(session, observable)


@router.post(
    "/{case_id}/observables/file",
    response_model=ObservablePublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_file_observable(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:observable")],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
    observable_type: Annotated[str, Form()] = "file",
    message: Annotated[str, Form()] = "",
    tlp: Annotated[int, Form()] = 2,
    ioc: Annotated[bool, Form()] = False,
    sighted: Annotated[bool, Form()] = False,
) -> ObservablePublic:
    await assert_attachment_type(session, observable_type)
    sha256, size, content_type = await ingest_upload(storage, file)
    # data = content hash, so within-case dedup means the same file can't be added twice.
    if await obs_crud.find_case_observable(
        session, case_ctx.case.id, observable_type, sha256
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This file is already attached to the case as an observable",
        )
    observable = await obs_crud.create_case_observable(
        session,
        ObservableCreate(
            observable_type=observable_type,
            data=sha256,
            message=message,
            tlp=tlp,
            ioc=ioc,
            sighted=sighted,
        ),
        case_id=case_ctx.case.id,
        organisation_id=case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )
    await attach_observable_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        observable_id=observable.id,
        name=file.filename or sha256,
        organisation_id=case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )
    return await obs_crud.to_public(session, observable)


@router.get("/{case_id}/alerts", response_model=Page[AlertPublic])
async def list_case_alerts(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:alert")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[AlertPublic]:
    """Alerts promoted into this case (the 'Linked alerts' panel)."""
    alerts, total = await alert_crud.list_alerts_for_case(
        session, case_ctx.case.id, skip=skip, limit=limit
    )
    flagged = await flag_crud.flagged_ids(
        session,
        FlagEntityType.alert,
        [str(a.id) for a in alerts],
        case_ctx.organisation_id,
    )
    cfs = await cf_crud.values_for_entities(
        session, CustomFieldEntityType.alert, [str(a.id) for a in alerts]
    )
    items = []
    for a in alerts:
        pub = AlertPublic.model_validate(a, from_attributes=True)
        pub.flagged = str(a.id) in flagged
        pub.custom_fields = cfs.get(str(a.id), {})
        items.append(pub)
    return Page(items=items, total=total, skip=skip, limit=limit)


# --- Comments on a case ---

@router.get("/{case_id}/comments", response_model=Page[CommentPublic])
async def list_case_comments(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
    sort_order: str = "desc",
) -> Page[CommentPublic]:
    comments, total = await comment_crud.list_comments(
        session, CommentEntityType.case, str(case_ctx.case.id), skip=skip, limit=limit, sort_order=sort_order
    )
    # Resolve author display names from user emails
    user_ids = [uuid.UUID(c.created_by) for c in comments]
    emails = await user_crud.emails_for_ids(session, user_ids)
    items = [
        CommentPublic(
            id=c.id,
            entity_type=c.entity_type,
            entity_id=c.entity_id,
            message=c.message,
            organisation_id=c.organisation_id,
            created_at=c.created_at,
            created_by=c.created_by,
            updated_at=c.updated_at,
            author_name=_display_name_from_email(
                emails.get(uuid.UUID(c.created_by), "")
            ),
        )
        for c in comments
    ]
    return Page(items=items, total=total, skip=skip, limit=limit)


@router.post(
    "/{case_id}/comments",
    response_model=CommentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_comment(
    comment_in: CommentCreate,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentPublic:
    comment = await comment_crud.create_comment(
        session,
        comment_in,
        entity_type=CommentEntityType.case,
        entity_id=str(case_ctx.case.id),
        organisation_id=case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )
    return CommentPublic(
        id=comment.id,
        entity_type=comment.entity_type,
        entity_id=comment.entity_id,
        message=comment.message,
        organisation_id=comment.organisation_id,
        created_at=comment.created_at,
        created_by=comment.created_by,
        updated_at=comment.updated_at,
        author_name=_display_name_from_email(case_ctx.user.email),
    )


# --- Tags on a case ---

@router.get("/{case_id}/tags", response_model=list[str])
async def list_case_tags(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.case, str(case_ctx.case.id)
    )


@router.put("/{case_id}/tags", response_model=list[str])
async def set_case_tags(
    body: TagSetRequest,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[str]:
    await tag_crud.set_tags(session, TaggableType.case, str(case_ctx.case.id), body.tags)
    return await tag_crud.list_tag_strings_for(
        session, TaggableType.case, str(case_ctx.case.id)
    )


# --- Custom fields on a case ---

@router.get("/{case_id}/custom-fields", response_model=dict[str, Any])
async def get_case_custom_fields(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    return await cf_crud.values_for(
        session, CustomFieldEntityType.case, str(case_ctx.case.id)
    )


@router.get("/{case_id}/counts", response_model=CaseCounts)
async def get_case_counts(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaseCounts:
    """Per-section counts for the detail page's tab badges. Each list CRUD is
    called with ``limit=0`` so only its COUNT runs (no rows materialised)."""
    case_id = case_ctx.case.id
    _, tasks_total = await task_crud.list_tasks_for_case(
        session,
        case_id,
        organisation_id=case_ctx.organisation_id,
        is_owner=case_ctx.is_owner,
        limit=0,
    )
    _, observables_total = await obs_crud.list_observables_for_case(
        session,
        case_id,
        organisation_id=case_ctx.organisation_id,
        is_owner=case_ctx.is_owner,
        limit=0,
    )
    _, comments_total = await comment_crud.list_comments(
        session, CommentEntityType.case, str(case_id), limit=0
    )
    _, attachments_total = await attachment_crud.list_links(
        session, case_id, case_only=True, limit=0
    )
    custom_fields = await cf_crud.values_for(
        session, CustomFieldEntityType.case, str(case_id)
    )
    similar = await obs_crud.similar_cases_for_case(
        session, case_id, organisation_id=case_ctx.organisation_id
    )
    return CaseCounts(
        tasks=tasks_total,
        custom_fields=len(custom_fields),
        comments=comments_total,
        attachments=attachments_total,
        observables=observables_total,
        similar=len(similar),
    )


@router.get("/{case_id}/similar", response_model=list[SimilarCasePublic])
async def list_case_similar_cases(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SimilarCasePublic]:
    """Cases sharing one or more observables with this case (this case itself and
    merged/duplicated tombstones are excluded). Fixed limit of 20."""
    rows = await obs_crud.similar_cases_for_case(
        session,
        case_ctx.case.id,
        organisation_id=case_ctx.organisation_id,
        limit=20,
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


@router.put("/{case_id}/custom-fields", response_model=dict[str, Any])
async def set_case_custom_fields(
    body: CustomFieldValuesSet,
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    try:
        values = await cf_crud.set_values(
            session,
            CustomFieldEntityType.case,
            str(case_ctx.case.id),
            case_ctx.organisation_id,
            body.values,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await audit_crud.record_audit(
        session,
        action="update",
        obj=case_ctx.case,
        context=case_ctx.case,
        actor=str(case_ctx.user.id),
        details={"custom_fields": values},
        organisation_id=case_ctx.organisation_id,
    )
    return values


# --- Case attachments ---


@router.get("/{case_id}/attachments", response_model=Page[AttachmentPublic])
async def list_case_attachments(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
    skip: int = 0,
    limit: int = 100,
) -> Page[AttachmentPublic]:
    # Case-owned attachments only (owner is the case itself). Task/log attachments
    # are listed under their owner; all share the per-case A-{case_id}-{id} counter.
    rows, total = await attachment_crud.list_links(
        session, case_ctx.case.id, case_only=True, skip=skip, limit=limit,
    )
    return Page(
        items=[attachment_crud.to_public(link, blob) for link, blob in rows],
        total=total, skip=skip, limit=limit,
    )


@router.post(
    "/{case_id}/attachments",
    response_model=AttachmentPublic,
    status_code=status.HTTP_201_CREATED,
)
async def upload_case_attachment(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form()] = None,
) -> AttachmentPublic:
    sha256, size, content_type = await ingest_upload(storage, file)
    link = await attach_case_blob(
        session,
        sha256=sha256,
        size=size,
        content_type=content_type,
        case_id=case_ctx.case.id,
        name=name or file.filename or sha256,
        organisation_id=case_ctx.organisation_id,
        created_by=str(case_ctx.user.id),
    )
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return attachment_crud.to_public(link, blob)


@router.get("/{case_id}/attachments/{attachment_id}/file")
async def download_case_attachment(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("read:case")],
    attachment_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[BlobStorage, Depends(get_storage)],
):
    link = await attachment_crud.get_link(session, case_ctx.case.id, attachment_id)
    if link is None or link.owner_task_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    blob = await attachment_crud.get_blob(session, link.attachment_id)
    return stream_blob(storage, link, blob)


@router.delete(
    "/{case_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_case_attachment(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("write:case")],
    attachment_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    link = await attachment_crud.get_link(session, case_ctx.case.id, attachment_id)
    if link is None or link.owner_task_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found"
        )
    await attachment_crud.delete_link(
        session, link, deleted_by=str(case_ctx.user.id)
    )


# --- Plugin runs (on-demand responders) ---


@router.post("/{case_id}/plugin-runs")
async def run_plugin_for_case(
    case_ctx: Annotated[CaseAuthContext, require_case_permission("run:enrichment")],
    body: ManualPluginRunRequest,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Dispatch a responder (or any plugin) on demand against this case. Mirrors
    ``POST /observables/{id}/plugin-runs``: visibility-checked (404 if the caller
    can't see the case), gated on ``run:enrichment``, and returns the synthetic
    queued-run view. ``force`` re-runs past retention dedup."""
    from app.api.v1.routes.plugins import create_manual_plugin_run

    return await create_manual_plugin_run(
        session,
        ctx,
        plugin_id=body.plugin_id,
        entity_type="case",
        entity_id=str(case_ctx.case.id),
        force=body.force,
    )
