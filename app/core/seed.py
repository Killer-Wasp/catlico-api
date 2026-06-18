from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select


async def seed_local_demo_data(session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from app.crud import case_ as case_crud
    from app.crud import comment as comment_crud
    from app.crud import custom_field as cf_crud
    from app.crud import observable as obs_crud
    from app.crud import organisation as org_crud
    from app.crud import organisation_member as member_crud
    from app.crud import role as role_crud
    from app.crud import tag as tag_crud
    from app.crud import task as task_crud
    from app.crud.user import create_user, get_user_by_email
    from app.models.case_ import Case, CaseCreate
    from app.models.comment import CommentCreate, CommentEntityType
    from app.models.custom_field import (
        CustomFieldCreate,
        CustomFieldEntityType,
        CustomFieldType,
    )
    from app.models.observable import ObservableCreate
    from app.models.organisation import OrganisationCreate
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.tag import TaggableType
    from app.models.task import TaskCreate, TaskStatus, TaskUpdate
    from app.models.user import UserCreate

    demo_title = "OAuth consent grant — privileged account compromise"
    existing = (
        await session.execute(select(Case).where(Case.title == demo_title))
    ).scalar_one_or_none()
    if existing is not None:
        return

    actor = "system"
    org = await org_crud.get_organisation(session, "catlico-demo")
    if org is None:
        org = await org_crud.create_organisation(
            session,
            OrganisationCreate(
                id="catlico-demo",
                name="Catlico Demo SOC",
                description="Local-only demo organisation seeded for development.",
            ),
            created_by=actor,
        )

    analyst = await get_user_by_email(session, "analyst@example.com")
    if analyst is None:
        analyst = await create_user(
            session,
            UserCreate(email="analyst@example.com", password="changeme"),
        )

    admin_role = await role_crud.get_role_by_name(session, "org-admin")
    if admin_role is None:
        raise RuntimeError("Built-in org-admin role must exist before demo seeding")

    if await member_crud.get_member(session, analyst.id, org.id) is None:
        await member_crud.add_member(
            session,
            org.id,
            OrganisationMemberCreate(user_id=analyst.id, role_id=admin_role.id),
            created_by=actor,
        )

    opened_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=3)
    case = await case_crud.create_case(
        session,
        CaseCreate(
            title=demo_title,
            description=(
                "Defender XDR raised AL-9119 after an unverified multi-tenant "
                "application was granted Mail.ReadWrite and offline_access by "
                "three privileged users.\n\n"
                "Consent grants followed a credential-phish lure referencing a "
                "fake billing portal. One service mailbox shows suspicious rule "
                "creation after consent."
            ),
            severity=3,
            tlp=2,
            pap=2,
            assignee_id=analyst.id,
            start_date=opened_at,
            summary="Containment is underway; tokens have been revoked for the affected accounts.",
        ),
        owner_org_id=org.id,
        owner_role_id=admin_role.id,
        created_by=str(analyst.id),
    )

    for name, display, field_type in [
        ("business_unit", "Business unit", CustomFieldType.string),
        ("campaign_id", "Campaign ID", CustomFieldType.string),
        ("affected_users", "Affected users", CustomFieldType.integer),
    ]:
        if await cf_crud.get_field_by_name(session, name, org.id) is None:
            await cf_crud.create_field(
                session,
                CustomFieldCreate(
                    name=name,
                    display_name=display,
                    field_type=field_type,
                ),
                organisation_id=org.id,
                created_by=actor,
            )

    await cf_crud.set_values(
        session,
        CustomFieldEntityType.case,
        str(case.id),
        org.id,
        {
            "business_unit": "Corporate IT",
            "campaign_id": "BILL-2026-Q2",
            "affected_users": 3,
        },
    )
    await tag_crud.set_tags(
        session,
        TaggableType.case,
        str(case.id),
        ["T1528", "T1566.002", "identity", "bec"],
    )

    task_specs = [
        (
            TaskCreate(
                title="Triage consent grant alert and confirm scope",
                group="Identify",
                description="Confirm which accounts, scopes, and application ids are involved.",
                assignee_id=analyst.id,
                order=1,
                start_date=opened_at + timedelta(minutes=6),
                due_date=opened_at + timedelta(hours=1),
            ),
            TaskStatus.completed,
        ),
        (
            TaskCreate(
                title="Revoke refresh tokens and reset credentials",
                group="Contain",
                description="Revoke active sessions for the affected accounts and force password resets.",
                assignee_id=analyst.id,
                order=2,
                start_date=opened_at + timedelta(minutes=28),
                due_date=opened_at + timedelta(hours=2),
            ),
            TaskStatus.completed,
        ),
        (
            TaskCreate(
                title="Disable malicious app registration tenant-wide",
                group="Contain",
                description="Block the app registration across the tenant and add it to policy.",
                assignee_id=analyst.id,
                order=3,
                start_date=opened_at + timedelta(minutes=45),
                due_date=opened_at + timedelta(hours=4),
            ),
            TaskStatus.in_progress,
        ),
        (
            TaskCreate(
                title="Hunt for the same app id across connected tenants",
                group="Hunt",
                description="Search for matching OAuth consent grants across every connected tenant.",
                order=4,
                due_date=opened_at + timedelta(days=1),
            ),
            TaskStatus.waiting,
        ),
    ]
    for task_in, status in task_specs:
        task = await task_crud.create_task(
            session,
            task_in,
            case_id=case.id,
            organisation_id=org.id,
            created_by=str(analyst.id),
        )
        if status != TaskStatus.waiting:
            await task_crud.update_task(
                session,
                task,
                TaskUpdate(status=status),
                updated_by=str(analyst.id),
            )

    for obs_in in [
        ObservableCreate(
            observable_type="domain",
            data="login-originenergy.support",
            message="Lookalike billing portal domain",
            tlp=2,
            ioc=True,
            sighted=True,
        ),
        ObservableCreate(
            observable_type="url",
            data="hxxps://cdn-au-billing[.]net/invoice.php",
            message="URLscan complete",
            tlp=2,
            ioc=True,
        ),
        ObservableCreate(
            observable_type="mail",
            data="accounts@billing-origin.co",
            message="Sender observed in phish lure",
            tlp=2,
            ioc=True,
            sighted=True,
        ),
        ObservableCreate(
            observable_type="ip",
            data="203.0.113.47",
            message="AbuseIPDB 97%",
            tlp=2,
            ioc=True,
            sighted=True,
        ),
    ]:
        await obs_crud.create_case_observable(
            session,
            obs_in,
            case_id=case.id,
            organisation_id=org.id,
            created_by=str(analyst.id),
        )

    for message in [
        "@analyst audit logs show consent grants preceded by the billing lure for 2 of 3 users.",
        "Tokens revoked and credentials reset. Escalating severity to High while app block is pending.",
    ]:
        await comment_crud.create_comment(
            session,
            CommentCreate(message=message),
            entity_type=CommentEntityType.case,
            entity_id=str(case.id),
            organisation_id=org.id,
            created_by=str(analyst.id),
        )

    await session.commit()
