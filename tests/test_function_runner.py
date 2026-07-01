"""D2: Function runner tests — success, failure, disabled mode, timeout, audit."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.configs import settings
from app.crud.function import create_function, create_run
from app.models.function import (
    FunctionCreate,
    FunctionRunStatus,
)


@pytest.fixture(autouse=True)
def _stub_runner_mode(monkeypatch):
    """Ensure function runner is in stub mode during tests."""
    monkeypatch.setattr(settings, "FUNCTION_RUNNER_MODE", "stub")


@pytest.fixture
async def a_function(session: AsyncSession, org_a, admin_user):
    func_in = FunctionCreate(
        name="test-func",
        code="print('hello')",
        runtime="python",
        timeout_ms=1000,
        enabled=True,
    )
    func = await create_function(
        session, func_in, organisation_id=org_a.id, created_by=str(admin_user.id)
    )
    return func


@pytest.fixture
async def a_run(session: AsyncSession, a_function, admin_user):
    run = await create_run(
        session,
        function_id=a_function.id,
        trigger="manual",
        context_type="case",
        context_id="1",
        created_by=str(admin_user.id),
    )
    await session.commit()
    return run


async def test_stub_runner_marks_success(session: AsyncSession, a_function, a_run):
    """Stub runner marks a queued run as successful and writes audit."""
    from app.services.function_runner import _execute_function

    await _execute_function(session, a_function, a_run)
    await session.refresh(a_run)
    assert a_run.status == FunctionRunStatus.success
    assert a_run.output is not None
    assert "test runner" in str(a_run.output)


async def test_stub_runner_writes_audit(session: AsyncSession, a_function, a_run):
    """Stub runner writes an audit row on success."""
    from app.services.function_runner import _execute_function

    await _execute_function(session, a_function, a_run)
    await session.commit()

    from sqlmodel import select
    from app.models.audit import Audit

    result = await session.execute(
        select(Audit).where(Audit.action == "function.run.completed")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1


async def test_disabled_mode_marks_failure(
    session: AsyncSession, a_function, a_run, monkeypatch
):
    """When FUNCTION_RUNNER_MODE=disabled, runs are marked as failure."""
    monkeypatch.setattr(settings, "FUNCTION_RUNNER_MODE", "disabled")
    from app.services.function_runner import _execute_function

    await _execute_function(session, a_function, a_run)
    await session.refresh(a_run)
    assert a_run.status == FunctionRunStatus.failure
    assert "sandbox not available" in (a_run.error or "")


async def test_stub_mode_fails_closed_outside_local(
    session: AsyncSession, a_function, a_run, monkeypatch
):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    from app.services.function_runner import _execute_function

    await _execute_function(session, a_function, a_run)
    await session.refresh(a_run)
    assert a_run.status == FunctionRunStatus.failure
    assert "only allowed when ENVIRONMENT=local" in (a_run.error or "")


async def test_disabled_mode_writes_failure_audit(
    session: AsyncSession, a_function, a_run, monkeypatch
):
    """Disabled mode writes a failure audit row."""
    monkeypatch.setattr(settings, "FUNCTION_RUNNER_MODE", "disabled")
    from app.services.function_runner import _execute_function

    await _execute_function(session, a_function, a_run)
    await session.commit()

    from sqlmodel import select
    from app.models.audit import Audit

    result = await session.execute(
        select(Audit).where(Audit.action == "function.run.failed")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1


async def test_timeout_handling(
    session: AsyncSession, a_function, a_run, monkeypatch
):
    """Timeout errors are caught at the poller level and marked as timeout status."""
    monkeypatch.setattr(settings, "FUNCTION_RUNNER_MODE", "stub")

    from app.services.function_runner import _process_queued_runs

    async def _slow_stub(*args, **kwargs):
        raise asyncio.TimeoutError("timed out")

    monkeypatch.setattr("app.services.function_runner.asyncio.sleep", _slow_stub)

    # _process_queued_runs catches TimeoutError and marks the run as timeout
    await session.commit()  # commit the queued run so the poller can see it
    count = await _process_queued_runs(session)
    assert count == 1
    await session.refresh(a_run)
    assert a_run.status == FunctionRunStatus.timeout
