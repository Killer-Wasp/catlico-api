from datetime import UTC, datetime

from sqlmodel import select

from app.crud import alert as alert_crud
from app.crud import audit as audit_crud
from app.crud import case_ as case_crud
from app.crud import observable as observable_crud
from app.crud import task as task_crud
from app.models.alert import AlertCreate
from app.models.audit import Audit, AuditOutbox
from app.models.case_ import Case, CaseCreate, CaseUpdate
from app.models.observable import ObservableCreate
from app.models.task import TaskCreate


async def _a_case(session) -> Case:
    case = Case(title="audit subject", created_by="system")
    session.add(case)
    await session.flush()
    return case


async def test_record_audit_writes_audit_and_outbox(session):
    case = await _a_case(session)

    audit = await audit_crud.record_audit(
        session,
        action="create",
        obj=case,
        context=case,
        actor="user-123",
        details={"title": "audit subject"},
    )

    assert audit.id is not None
    assert audit.object_type == "case"  # derived from __tablename__
    assert audit.object_id == str(case.id)
    assert audit.context_type == "case"
    assert audit.context_id == str(case.id)
    assert audit.actor == "user-123"
    assert audit.request_id  # minted on demand for non-HTTP callers

    outbox = (
        (await session.execute(select(AuditOutbox).where(AuditOutbox.audit_id == audit.id)))
        .scalars()
        .all()
    )
    assert len(outbox) == 1
    row = outbox[0]
    assert row.topic == "audit"
    assert row.delivered_at is None
    assert row.payload["object_type"] == "case"
    assert row.payload["action"] == "create"


async def test_record_audit_redacts_secrets(session):
    case = await _a_case(session)

    audit = await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        actor="system",
        details={
            "title": "visible",
            "password": "hunter2",
            "new_password": "s3cret",
            "api_key": "abcd",
            "config": {"k": "v"},
        },
    )

    assert audit.details["title"] == "visible"  # benign field kept
    assert audit.details["password"] == "[redacted]"
    assert audit.details["new_password"] == "[redacted]"
    assert audit.details["api_key"] == "[redacted]"
    assert audit.details["config"] == "[redacted]"
    # The secret values themselves never appear anywhere in the row.
    assert "hunter2" not in str(audit.details)
    assert "s3cret" not in str(audit.details)


async def test_record_audit_coerces_non_serialisable_details(session):
    case = await _a_case(session)
    when = datetime.now(UTC)

    # A datetime in details must not raise / 500 the mutation — it's coerced to str.
    audit = await audit_crud.record_audit(
        session,
        action="update",
        obj=case,
        actor="system",
        details={"when": when, "nested": {"ids": {1, 2}}},
    )

    assert isinstance(audit.details["when"], str)
    assert audit.details["nested"]["ids"] == [1, 2] or set(
        audit.details["nested"]["ids"]
    ) == {1, 2}


async def test_dispatch_marks_pending_delivered(session):
    case = await _a_case(session)
    await audit_crud.record_audit(session, action="create", obj=case, actor="system")
    await audit_crud.record_audit(session, action="update", obj=case, actor="system")

    processed = await audit_crud.dispatch_pending_outbox(session)
    assert processed == 2

    rows = (await session.execute(select(AuditOutbox))).scalars().all()
    assert rows  # sanity
    assert all(r.delivered_at is not None for r in rows)
    assert all(r.attempts == 1 for r in rows)

    # Second drain finds nothing left to do.
    assert await audit_crud.dispatch_pending_outbox(session) == 0


async def test_dispatch_invokes_registered_consumer(session):
    from app.crud.audit import _consumers

    case = await _a_case(session)
    await audit_crud.record_audit(
        session, action="create", obj=case, actor="system", organisation_id="test-org"
    )

    seen: list[dict] = []

    async def consumer(s, row) -> None:
        seen.append(row.payload or {})

    # Clear global consumers for this test
    saved = list(_consumers)
    _consumers.clear()
    try:
        audit_crud.register_consumer(consumer)
        await audit_crud.dispatch_pending_outbox(session)
    finally:
        _consumers.clear()
        _consumers.extend(saved)

    assert len(seen) == 1
    assert seen[0]["action"] == "create"


async def _latest_outbox(session) -> AuditOutbox:
    """The most recently written outbox row — used to inspect the payload a mutation
    just produced."""
    return (
        (await session.execute(select(AuditOutbox).order_by(AuditOutbox.id.desc())))
        .scalars()
        .first()
    )


# --- Product mutations stamp organisation_id into the outbox payload ---------
# These prove the feed-visibility fix: each mutation's outbox row now carries the
# acting org, which GET /events and the WS /activity stream filter on.


async def test_create_case_outbox_carries_org(session, org_a, builtin_roles, analyst_a):
    await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    assert (await _latest_outbox(session)).payload["organisation_id"] == org_a.id


async def test_update_case_outbox_carries_org(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await case_crud.update_case(
        session,
        case,
        CaseUpdate(title="c2"),
        updated_by=str(analyst_a.id),
        organisation_id=org_a.id,
    )
    row = await _latest_outbox(session)
    assert row.payload["action"] == "update"
    assert row.payload["organisation_id"] == org_a.id


async def test_delete_case_outbox_carries_org(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await case_crud.delete_case(
        session, case, deleted_by=str(analyst_a.id), organisation_id=org_a.id
    )
    row = await _latest_outbox(session)
    assert row.payload["action"] == "delete"
    assert row.payload["organisation_id"] == org_a.id


async def test_create_alert_outbox_carries_org(session, org_a, analyst_a):
    await alert_crud.ingest_alert(
        session,
        AlertCreate(
            type="phishing", source="EDR", source_ref="x1", title="t", severity=2, tlp=2
        ),
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    row = await _latest_outbox(session)
    assert row.payload["object_type"] == "alert"
    assert row.payload["organisation_id"] == org_a.id


async def test_create_observable_outbox_carries_org(
    session, org_a, builtin_roles, analyst_a
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await observable_crud.create_case_observable(
        session,
        ObservableCreate(observable_type="ip", data="1.1.1.1"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    row = await _latest_outbox(session)
    assert row.payload["object_type"] == "observable"
    assert row.payload["organisation_id"] == org_a.id


async def test_create_task_outbox_carries_org(session, org_a, builtin_roles, analyst_a):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await task_crud.create_task(
        session,
        TaskCreate(title="triage"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    row = await _latest_outbox(session)
    assert row.payload["object_type"] == "task"
    assert row.payload["organisation_id"] == org_a.id


async def test_record_audit_is_transactional_with_mutation(session):
    """The audit row lives in the same session/transaction as the subject it
    describes — no separate commit."""
    before = (await session.execute(select(Audit))).scalars().all()
    case = await _a_case(session)
    await audit_crud.record_audit(session, action="create", obj=case, actor="system")
    after = (await session.execute(select(Audit))).scalars().all()
    assert len(after) == len(before) + 1
