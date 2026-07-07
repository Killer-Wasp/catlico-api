from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select


async def seed_local_demo_data(session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.demo_seed_data import (
        data_exfiltration_demo_tasks,
        demo_alert_specs,
        demo_knowledge_base_pages,
        oauth_demo_tasks,
        ransomware_demo_tasks,
    )
    from app.crud import alert as alert_crud
    from app.crud import case_ as case_crud
    from app.crud import comment as comment_crud
    from app.crud import custom_field as cf_crud
    from app.crud import knowledge_base as kb_crud
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
    from app.models.knowledge_base import KnowledgeBasePage
    from app.models.observable import ObservableCreate
    from app.models.organisation import OrganisationCreate
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.tag import TaggableType
    from app.models.task import Task, TaskCreate, TaskStatus, TaskUpdate
    from app.models.user import UserCreate

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

    # --- Inbound alerts ---
    now = datetime.now(UTC).replace(microsecond=0)
    for alert_in in demo_alert_specs(now):
        await alert_crud.ingest_alert(
            session,
            alert_in,
            organisation_id=org.id,
            created_by=actor,
        )

    for page_in in demo_knowledge_base_pages():
        existing_page = await session.execute(
            select(KnowledgeBasePage.id).where(
                KnowledgeBasePage.organisation_id == org.id,
                KnowledgeBasePage.title == page_in.title,
                KnowledgeBasePage.deleted_at.is_(None),
            )
        )
        if existing_page.scalar_one_or_none() is None:
            await kb_crud.create_page(
                session,
                page_in,
                organisation_id=org.id,
                actor=analyst,
            )

    # --- Case 1: OAuth consent grant — privileged account compromise ---
    demo_title = "OAuth consent grant — privileged account compromise"
    demo_check = await session.execute(
        select(Case.id).where(Case.title == demo_title).limit(1)
    )
    demo_exists = demo_check.scalar_one_or_none() is not None
    if not demo_exists:
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

    # --- Case 2: Ransomware precursor activity ---
    rw_title = "Ransomware precursor activity — lateral movement detected"
    rw_check = await session.execute(
        select(Case.id).where(Case.title == rw_title).limit(1)
    )
    rw_exists = rw_check.scalar_one_or_none() is not None
    if not rw_exists:
        rw_case = await case_crud.create_case(
            session,
            CaseCreate(
                title=rw_title,
                description=(
                    "CrowdStrike detected suspicious PsExec usage from SRV-APP01 to "
                    "multiple workstations in the Finance VLAN. Command-and-control "
                    "callbacks observed to 45.155.205.233. Ransomware deployment appears "
                    "imminent based on TTPs and dwell time analysis.\n\n"
                    "Affected subnets: 10.23.1.0/24, 10.23.4.0/24"
                ),
                severity=4,
                tlp=2,
                pap=2,
                assignee_id=analyst.id,
                start_date=now - timedelta(hours=4),
                summary="Isolated affected VLANs. IR retainers activated. Awaiting disk images from SRV-APP01.",
            ),
            owner_org_id=org.id,
            owner_role_id=admin_role.id,
            created_by=str(analyst.id),
        )
        await tag_crud.set_tags(
            session,
            TaggableType.case,
            str(rw_case.id),
            ["T1486", "T1021.002", "T1071.001", "ransomware", "cobalt-strike"],
        )
        rw_tasks = [
            (
                TaskCreate(
                    title="Isolate affected hosts from network",
                    group="Contain",
                    description="Network-contain SRV-APP01 and all workstations with C2 callbacks.",
                    assignee_id=analyst.id,
                    order=1,
                    start_date=now - timedelta(hours=3),
                    due_date=now - timedelta(hours=2),
                ),
                TaskStatus.completed,
            ),
            (
                TaskCreate(
                    title="Acquire forensic images and memory dumps",
                    group="Identify",
                    description="Capture full disk images and RAM from SRV-APP01 and affected endpoints.",
                    assignee_id=analyst.id,
                    order=2,
                    start_date=now - timedelta(hours=3),
                    due_date=now + timedelta(hours=2),
                ),
                TaskStatus.in_progress,
            ),
            (
                TaskCreate(
                    title="Block C2 infrastructure at perimeter",
                    group="Contain",
                    description="Add 45.155.205.233 and associated domains to blocklist on perimeter firewalls.",
                    assignee_id=analyst.id,
                    order=3,
                    due_date=now + timedelta(hours=1),
                ),
                TaskStatus.waiting,
            ),
        ]
        for task_in, status in rw_tasks:
            task = await task_crud.create_task(
                session,
                task_in,
                case_id=rw_case.id,
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
                observable_type="ip",
                data="45.155.205.233",
                message="Cobalt Strike C2 server — active beaconing",
                tlp=2,
                ioc=True,
                sighted=True,
            ),
            ObservableCreate(
                observable_type="hash",
                data="d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3",
                message="SHA256 of PsExec binary dropped on SRV-APP01",
                tlp=2,
                ioc=True,
            ),
            ObservableCreate(
                observable_type="domain",
                data="update-winsec[.]top",
                message="C2 domain resolving to 45.155.205.233",
                tlp=2,
                ioc=True,
                sighted=True,
            ),
        ]:
            await obs_crud.create_case_observable(
                session,
                obs_in,
                case_id=rw_case.id,
                organisation_id=org.id,
                created_by=str(analyst.id),
            )
        await comment_crud.create_comment(
            session,
            CommentCreate(message="Containment initiated — Finance VLAN isolated. IR team engaged."),
            entity_type=CommentEntityType.case,
            entity_id=str(rw_case.id),
            organisation_id=org.id,
            created_by=str(analyst.id),
        )

    # --- Case 3: Data exfiltration via unapproved SaaS ---
    dx_title = "Data exfiltration via unapproved SaaS application"
    dx_check = await session.execute(
        select(Case.id).where(Case.title == dx_title).limit(1)
    )
    dx_exists = dx_check.scalar_one_or_none() is not None
    if not dx_exists:
        dx_case = await case_crud.create_case(
            session,
            CaseCreate(
                title=dx_title,
                description=(
                    "CASB alerted on unusually large data transfer from OneDrive to an "
                    "unapproved third-party file-sharing service (tempfileshare[.]io). "
                    "Approximately 4.2 GB of files with 'PII' and 'Financial' sensitivity "
                    "labels were exfiltrated over a 45-minute window.\n\n"
                    "User account: marcus.johnson@example.com"
                ),
                severity=3,
                tlp=2,
                pap=2,
                assignee_id=analyst.id,
                start_date=now - timedelta(hours=8),
                summary="User account disabled pending investigation. Legal notified for potential regulatory reporting.",
            ),
            owner_org_id=org.id,
            owner_role_id=admin_role.id,
            created_by=str(analyst.id),
        )
        await tag_crud.set_tags(
            session,
            TaggableType.case,
            str(dx_case.id),
            ["T1048.002", "T1567", "exfiltration", "insider", "data-loss"],
        )
        dx_tasks = [
            (
                TaskCreate(
                    title="Disable user account and revoke sessions",
                    group="Contain",
                    description="Disable marcus.johnson@example.com and revoke all active tokens/sessions.",
                    assignee_id=analyst.id,
                    order=1,
                    start_date=now - timedelta(hours=7),
                    due_date=now - timedelta(hours=6),
                ),
                TaskStatus.completed,
            ),
            (
                TaskCreate(
                    title="Audit file access and transfer logs",
                    group="Identify",
                    description="Review OneDrive audit logs, CASB logs, and endpoint DLP events for the affected user.",
                    assignee_id=analyst.id,
                    order=2,
                    start_date=now - timedelta(hours=6),
                    due_date=now + timedelta(hours=4),
                ),
                TaskStatus.in_progress,
            ),
            (
                TaskCreate(
                    title="Request takedown of exfil files from SaaS provider",
                    group="Contain",
                    description="Contact tempfileshare.io to request removal of exfiltrated data and obtain access logs.",
                    order=3,
                    due_date=now + timedelta(hours=6),
                ),
                TaskStatus.waiting,
            ),
            (
                TaskCreate(
                    title="Prepare regulatory notification draft",
                    group="Report",
                    description="Draft notification for affected data subjects per regulatory requirements. Coordinate with Legal.",
                    order=4,
                    due_date=now + timedelta(days=2),
                ),
                TaskStatus.waiting,
            ),
        ]
        for task_in, status in dx_tasks:
            task = await task_crud.create_task(
                session,
                task_in,
                case_id=dx_case.id,
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
                data="tempfileshare[.]io",
                message="Unapproved file-sharing service used for exfiltration",
                tlp=2,
                ioc=True,
                sighted=True,
            ),
            ObservableCreate(
                observable_type="mail",
                data="marcus.johnson@example.com",
                message="User account associated with data exfiltration",
                tlp=2,
                ioc=True,
                sighted=True,
            ),
            ObservableCreate(
                observable_type="url",
                data="hxxps://tempfileshare[.]io/d/4f8a2c91b3d6",
                message="Exfiltration download link identified in CASB logs",
                tlp=2,
                ioc=True,
            ),
        ]:
            await obs_crud.create_case_observable(
                session,
                obs_in,
                case_id=dx_case.id,
                organisation_id=org.id,
                created_by=str(analyst.id),
            )
        await comment_crud.create_comment(
            session,
            CommentCreate(message="User account disabled. Confirmed 4.2 GB of data transferred. Legal team notified."),
            entity_type=CommentEntityType.case,
            entity_id=str(dx_case.id),
            organisation_id=org.id,
            created_by=str(analyst.id),
        )

    async def ensure_demo_task(
        case_title: str,
        task_in: TaskCreate,
        status: TaskStatus = TaskStatus.waiting,
    ) -> None:
        case_id = (
            await session.execute(select(Case.id).where(Case.title == case_title))
        ).scalar_one_or_none()
        if case_id is None:
            return
        existing = await session.execute(
            select(Task).where(Task.case_id == case_id, Task.title == task_in.title)
        )
        if existing.scalar_one_or_none() is not None:
            return
        task = await task_crud.create_task(
            session,
            task_in,
            case_id=case_id,
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

    for task_in, status in oauth_demo_tasks(now, analyst.id):
        await ensure_demo_task(demo_title, task_in, status)

    for task_in, status in ransomware_demo_tasks(now, analyst.id):
        await ensure_demo_task(rw_title, task_in, status)

    for task_in, status in data_exfiltration_demo_tasks(now, analyst.id):
        await ensure_demo_task(dx_title, task_in, status)

    await session.commit()
