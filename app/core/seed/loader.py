"""Generic, data-driven seeder.

Reads a JSON profile (see :mod:`app.core.seed.schema`) and applies it to the
database, preserving the exact behaviour of the old hand-written demo seeder:
alert→case promotion, custom fields, observables, comments, backdated resolved/
open cases (for the dashboard trend + MTTR), work logs, SLA policies and saved
dashboards. Every step is idempotent, so re-running back-fills without
duplicating.

The *content* lives in JSON; the *wiring* (who is the creator/actor, promotion,
backdating mechanics) lives here — JSON cannot express those.
"""

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.seed.schema import (
    PROFILE_FILES,
    SeedOrganisation,
    SeedProfile,
    parse_offset,
)

SEED_DATA_DIR = Path(__file__).resolve().parent.parent / "seed_data"


def profile_dir(profile: str) -> Path:
    return SEED_DATA_DIR / profile


def load_profile(profile: str) -> SeedProfile:
    """Read and validate a profile directory into a :class:`SeedProfile`."""
    base = profile_dir(profile)
    if not base.is_dir():
        raise FileNotFoundError(f"Seed profile directory not found: {base}")

    org_path = base / "organisation.json"
    if not org_path.exists():
        raise FileNotFoundError(f"Seed profile missing organisation.json: {base}")
    org = SeedOrganisation.model_validate(json.loads(org_path.read_text()))

    lists: dict[str, list] = {}
    for filename, (key, attr, model) in PROFILE_FILES.items():
        path = base / filename
        items: list = []
        if path.exists():
            raw = json.loads(path.read_text())
            items = [model.model_validate(item) for item in raw.get(key, [])]
        lists[attr] = items

    return SeedProfile(org=org, **lists)


async def seed_from_profile(session: AsyncSession, profile: str) -> None:
    from datetime import UTC, datetime

    from app.crud import alert as alert_crud
    from app.crud import case_ as case_crud
    from app.crud import comment as comment_crud
    from app.crud import custom_field as cf_crud
    from app.crud import knowledge_base as kb_crud
    from app.crud import log as log_crud
    from app.crud import observable as obs_crud
    from app.crud import organisation as org_crud
    from app.crud import organisation_member as member_crud
    from app.crud import role as role_crud
    from app.crud import sla as sla_crud
    from app.crud import tag as tag_crud
    from app.crud import task as task_crud
    from app.crud.user import create_user, get_user_by_email, set_password
    from app.crud import case_status as case_status_crud
    from app.models.alert import Alert, AlertCreate
    from app.models.case_ import Case, CaseCreate, CaseResolutionStatus
    from app.models.case_status import CaseStage
    from app.models.comment import CommentCreate, CommentEntityType
    from app.models.custom_field import (
        CustomFieldCreate,
        CustomFieldEntityType,
        CustomFieldType,
    )
    from app.models.dashboard import Dashboard
    from app.models.knowledge_base import (
        KnowledgeBasePage,
        KnowledgeBasePageCreate,
    )
    from app.models.log import Log, LogCreate
    from app.models.observable import ObservableCreate
    from app.models.organisation import OrganisationCreate
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.sla import SlaPolicyUpsert
    from app.models.tag import TaggableType
    from app.models.task import Task, TaskCreate, TaskStatus, TaskUpdate
    from app.models.user import User, UserCreate

    data = load_profile(profile)
    now = datetime.now(UTC).replace(microsecond=0)
    actor = "system"

    def at(offset: str | None):
        return now + parse_offset(offset) if offset else None

    # --- Organisation --------------------------------------------------------
    org = await org_crud.get_organisation(session, data.org.id)
    if org is None:
        org = await org_crud.create_organisation(
            session,
            OrganisationCreate(
                id=data.org.id,
                name=data.org.name,
                description=data.org.description,
            ),
            created_by=actor,
        )

    admin_role = await role_crud.get_role_by_name(session, "org-admin", org.id)
    if admin_role is None:
        raise RuntimeError("Built-in org-admin role must exist before seeding")

    # Ensure the org's built-in case statuses exist (idempotent) so resolved seed
    # cases can reference the Resolved status.
    builtin_statuses = await case_status_crud.seed_org_builtin_statuses(session, org.id)
    resolved_status = builtin_statuses[CaseStage.closed]

    # --- Users + memberships -------------------------------------------------
    users_by_email: dict[str, User] = {}
    primary_user: User | None = None
    for seed_user in data.org.users:
        role = await role_crud.get_role_by_name(session, seed_user.role, org.id)
        if role is None:
            raise RuntimeError(f"Role {seed_user.role!r} not found for seed user")
        user = await get_user_by_email(session, seed_user.email)
        if user is None:
            user = await create_user(
                session,
                UserCreate(
                    email=seed_user.email,
                    first_name=seed_user.first_name,
                    last_name=seed_user.last_name,
                ),
            )
            # create_user is always password-less; seed profiles ship a known
            # local password, so set it explicitly (internal caller — bypasses
            # the admin API's no-password boundary by design).
            if seed_user.password:
                await set_password(session, user, seed_user.password)
        if await member_crud.get_member(session, user.id, org.id) is None:
            await member_crud.add_member(
                session,
                org.id,
                OrganisationMemberCreate(user_id=user.id, role_id=role.id),
                created_by=actor,
            )
        users_by_email[seed_user.email] = user
        if seed_user.primary:
            primary_user = user

    if primary_user is None:
        raise RuntimeError("Seed profile must mark exactly one user as primary")
    creator = str(primary_user.id)

    def assignee_id(email: str | None):
        return users_by_email[email].id if email else None

    # --- Custom field definitions (org-level) --------------------------------
    for cfd in data.org.custom_field_definitions:
        if await cf_crud.get_field_by_name(session, cfd.name, org.id) is None:
            await cf_crud.create_field(
                session,
                CustomFieldCreate(
                    name=cfd.name,
                    display_name=cfd.display_name,
                    field_type=CustomFieldType(cfd.field_type),
                ),
                organisation_id=org.id,
                created_by=actor,
            )

    # --- Knowledge base pages ------------------------------------------------
    for page in data.pages:
        exists = (
            await session.execute(
                select(KnowledgeBasePage.id).where(
                    KnowledgeBasePage.organisation_id == org.id,
                    KnowledgeBasePage.title == page.title,
                    KnowledgeBasePage.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            await kb_crud.create_page(
                session,
                KnowledgeBasePageCreate(
                    title=page.title,
                    summary=page.summary,
                    tags=page.tags,
                    content=page.content,
                ),
                organisation_id=org.id,
                actor=primary_user,
            )

    # --- Alerts (ingested up front so cases can promote them by ref) ---------
    alerts_by_ref: dict[str, Alert] = {}
    for a in data.alerts:
        alert, _ = await alert_crud.ingest_alert(
            session,
            AlertCreate(
                type=a.type,
                source=a.source,
                source_ref=a.ref,
                title=a.title,
                description=a.description,
                severity=a.severity,
                tlp=a.tlp,
                date=at(a.date),
            ),
            organisation_id=org.id,
            created_by=actor,
        )
        alerts_by_ref[a.ref] = alert
        await tag_crud.set_tags(session, TaggableType.alert, str(alert.id), a.tags)

    async def promote(ref: str | None, case_id: int) -> None:
        if not ref:
            return
        origin = alerts_by_ref.get(ref)
        if origin is not None and origin.case_id is None:
            await alert_crud.mark_promoted(
                session, origin, case_id=case_id, updated_by=creator
            )

    # --- Cases (+ nested tags, custom fields, observables, comments) ---------
    cases_by_ref: dict[str, int] = {}
    for c in data.cases:
        existing_id = (
            await session.execute(
                select(Case.id).where(Case.title == c.title).limit(1)
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            cases_by_ref[c.ref] = existing_id
            continue

        case = await case_crud.create_case(
            session,
            CaseCreate(
                title=c.title,
                description=c.description,
                severity=c.severity,
                tlp=c.tlp,
                pap=c.pap,
                assignee_id=assignee_id(c.assignee),
                start_date=at(c.start),
                summary=c.summary,
            ),
            owner_org_id=org.id,
            owner_role_id=admin_role.id,
            created_by=creator,
        )

        # Backdate / resolve so the dashboard's trend line + MTTR have history.
        mutated = False
        if c.created:
            case.created_at = at(c.created)
            mutated = True
        if c.status == "resolved":
            case.status_id = resolved_status.id
            case.resolution_status = CaseResolutionStatus[c.resolution]
            resolved_at = at(c.resolved)
            case.end_date = resolved_at
            case.updated_at = resolved_at
            case.updated_by = creator
            mutated = True
        if mutated:
            session.add(case)
            await session.flush()

        await tag_crud.set_tags(session, TaggableType.case, str(case.id), c.tags)
        if c.custom_fields:
            await cf_crud.set_values(
                session,
                CustomFieldEntityType.case,
                str(case.id),
                org.id,
                dict(c.custom_fields),
            )
        for obs in c.observables:
            await obs_crud.create_case_observable(
                session,
                ObservableCreate(
                    observable_type=obs.observable_type,
                    data=obs.data,
                    message=obs.message,
                    tlp=obs.tlp,
                    ioc=obs.ioc,
                    sighted=obs.sighted,
                ),
                case_id=case.id,
                organisation_id=org.id,
                created_by=creator,
            )
        for comment in c.comments:
            await comment_crud.create_comment(
                session,
                CommentCreate(message=comment.message),
                entity_type=CommentEntityType.case,
                entity_id=str(case.id),
                organisation_id=org.id,
                created_by=creator,
            )
        await promote(c.link_alert, case.id)
        cases_by_ref[c.ref] = case.id

    # --- Tasks (+ nested work logs), idempotent by (case, title) -------------
    for t in data.tasks:
        case_id = cases_by_ref.get(t.case)
        if case_id is None:
            continue
        task = (
            await session.execute(
                select(Task).where(Task.case_id == case_id, Task.title == t.title)
            )
        ).scalar_one_or_none()
        if task is None:
            task = await task_crud.create_task(
                session,
                TaskCreate(
                    title=t.title,
                    group=t.group,
                    description=t.description,
                    assignee_id=assignee_id(t.assignee),
                    order=t.order,
                    start_date=at(t.start),
                    due_date=at(t.due),
                ),
                case_id=case_id,
                organisation_id=org.id,
                created_by=creator,
            )
            status = TaskStatus[t.status]
            if status != TaskStatus.waiting:
                await task_crud.update_task(
                    session, task, TaskUpdate(status=status), updated_by=creator
                )
        for entry in t.logs:
            already = (
                await session.execute(
                    select(Log.id).where(
                        Log.case_id == case_id,
                        Log.task_id == task.id,
                        Log.message == entry.message,
                        Log.deleted_at.is_(None),
                    )
                )
            ).first()
            if already is not None:
                continue
            await log_crud.create_log(
                session,
                LogCreate(message=entry.message, occurred_at=at(entry.occurred)),
                case_id=case_id,
                task_id=task.id,
                organisation_id=org.id,
                created_by=creator,
            )

    # --- SLA policies (upsert) -----------------------------------------------
    for policy in data.policies:
        await sla_crud.upsert_policy(
            session,
            SlaPolicyUpsert(
                severity=policy.severity,
                ack_seconds=policy.ack_seconds,
                resolve_seconds=policy.resolve_seconds,
                escalation_target=policy.escalation_target,
            ),
            organisation_id=org.id,
            created_by=actor,
        )

    # --- Dashboards (idempotent by (org, name)) ------------------------------
    superadmin = (
        await session.execute(select(User).where(User.is_superadmin.is_(True)).limit(1))
    ).scalar_one_or_none()

    for dash in data.dashboards:
        if dash.owner == "superadmin":
            if superadmin is None or superadmin.id == primary_user.id:
                continue
            owner_id = superadmin.id
        else:
            owner_id = primary_user.id
        exists = (
            await session.execute(
                select(Dashboard.id).where(
                    Dashboard.organisation_id == org.id,
                    Dashboard.name == dash.name,
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            Dashboard(
                organisation_id=org.id,
                name=dash.name,
                description=dash.description,
                layout={"widgets": dash.widgets},
                is_public=dash.shared,
                created_by=str(owner_id),
            )
        )

    await session.commit()
