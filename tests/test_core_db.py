from sqlmodel import select

from app.core.db import ensure_default_superadmin, init_db
from app.crud.user import get_user_by_email
from app.crud import tag as tag_crud
from app.models.audit import Audit
from app.models.alert import Alert
from app.models.case_ import Case
from app.models.comment import Comment
from app.models.knowledge_base import KnowledgeBasePage
from app.models.observable import Observable
from app.models.tag import TaggableType
from app.models.task import Task
from app.models.user import User


async def test_ensure_default_superadmin_creates_missing_user(session, monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "DEFAULT_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "secret123")

    await ensure_default_superadmin(session)
    user = await get_user_by_email(session, "root@example.com")
    assert user is not None
    assert user.is_superadmin is True
    assert user.is_active is True
    assert user.hashed_password


async def test_ensure_default_superadmin_rotates_password_when_changed(session, monkeypatch):
    from app.core.configs import settings
    from app.core.security import get_password_hash

    monkeypatch.setattr(settings, "DEFAULT_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "secret123")

    user = User(
        email="root@example.com",
        first_name="Root",
        last_name="Admin",
        hashed_password=get_password_hash("old-password"),
        is_superadmin=True,
        is_active=True,
    )
    old_hash = user.hashed_password
    session.add(user)
    await session.commit()

    await ensure_default_superadmin(session)
    updated = await get_user_by_email(session, "root@example.com")
    assert updated is not None
    assert updated.hashed_password != old_hash


async def test_init_db_skips_demo_case_seed_outside_local(session, monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    await init_db(session)

    cases = (await session.execute(select(Case))).scalars().all()
    assert cases == []


async def test_init_db_seeds_demo_case_once_in_local(session, monkeypatch):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "local")

    await init_db(session)
    await init_db(session)

    cases = (await session.execute(select(Case))).scalars().all()
    assert {case.title for case in cases} == {
        "OAuth consent grant — privileged account compromise",
        "Ransomware precursor activity — lateral movement detected",
        "Data exfiltration via unapproved SaaS application",
    }
    case = cases[0]

    tasks = (
        await session.execute(select(Task).where(Task.case_id == case.id))
    ).scalars().all()
    observables = (
        await session.execute(select(Observable).where(Observable.case_id == case.id))
    ).scalars().all()
    comments = (
        await session.execute(
            select(Comment).where(
                Comment.entity_type == "case",
                Comment.entity_id == str(case.id),
            )
        )
    ).scalars().all()
    activity = (
        await session.execute(
            select(Audit).where(
                Audit.context_type == "case",
                Audit.context_id == str(case.id),
            )
        )
    ).scalars().all()

    assert len(cases) == 3
    assert len(tasks) >= 1
    assert len(observables) >= 1
    assert len(comments) >= 1
    assert len(activity) >= 1


async def test_init_db_seeds_demo_operational_content_once_in_local(
    session, monkeypatch
):
    from app.core.configs import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "local")

    await init_db(session)
    await init_db(session)

    alerts = (await session.execute(select(Alert))).scalars().all()
    tasks = (await session.execute(select(Task))).scalars().all()
    pages = (await session.execute(select(KnowledgeBasePage))).scalars().all()

    assert {alert.title for alert in alerts} >= {
        "Possible ransomware staging — mass file rename on FILESRV-AU02",
        "OAuth consent grant to unverified app for 3 privileged users",
        "Credential-phish campaign targeting retail billing team (38 rcpts)",
    }
    alert_by_ref = {alert.source_ref: alert for alert in alerts}
    assert set(
        await tag_crud.list_tag_strings_for(
            session,
            TaggableType.alert,
            str(alert_by_ref["AL-9080"].id),
        )
    ) == {"certificate", "partner-api", "hygiene"}
    assert set(
        await tag_crud.list_tag_strings_for(
            session,
            TaggableType.alert,
            str(alert_by_ref["AL-9123"].id),
        )
    ) == {"ransomware", "T1486", "finance"}
    assert {task.title for task in tasks} >= {
        "Disable malicious app registration tenant-wide",
        "Remove mailbox rules and check forwarding",
        "Publish executive situation summary",
    }
    assert {page.title for page in pages} == {
        "Phishing response runbook",
        "BEC investigation guide",
        "TLP & PAP handling policy",
        "Analyst onboarding checklist",
    }
