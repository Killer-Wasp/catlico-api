from datetime import UTC, datetime

from sqlmodel import select

from app.crud import audit as audit_crud
from app.models.audit import Audit, AuditOutbox
from app.models.case_ import Case


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


async def test_record_audit_is_transactional_with_mutation(session):
    """The audit row lives in the same session/transaction as the subject it
    describes — no separate commit."""
    before = (await session.execute(select(Audit))).scalars().all()
    case = await _a_case(session)
    await audit_crud.record_audit(session, action="create", obj=case, actor="system")
    after = (await session.execute(select(Audit))).scalars().all()
    assert len(after) == len(before) + 1
