"""D2: Safe function runner abstraction.

The runner polls the queue, claims work, executes functions with scoped tokens,
and reports results. The `TestRunner` is the v1 implementation: it runs an
in-process stub that always succeeds (for manual test) without executing
arbitrary user code. A real sandboxed runner (subprocess isolation) is a later
milestone.

Security invariants (enforced by this module):
- Function runs as actor `function:<id>`, never as the triggering user
- Token is short-lived and scoped to the function + org + context
- No raw filesystem access, no process spawning from inside function runtime
- Secrets are referenced by name, resolved at runtime
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.crud.audit import record_audit
from app.crud.function import (
    create_run,
    list_functions,
    update_run_status,
)
from app.models.function import (
    Function,
    FunctionRun,
    FunctionRunStatus,
)
from app.services.function_tokens import create_function_token

logger = logging.getLogger(__name__)

# ponytail: poll interval for the function runner — config-ify if needed
RUNNER_POLL_INTERVAL = 5.0


async def _execute_function(
    session: AsyncSession, func: Function, run: FunctionRun
) -> None:
    """Execute a single function run. v1 test stub: mark success immediately.
    Real implementation would sandbox-execute func.code with the scoped token.

    ponytail: test stub; replace with subprocess/jail sandbox when needed.
    """
    token = create_function_token(
        function_id=func.id,
        organisation_id=func.organisation_id,
        context_type=run.context_type,
        context_id=run.context_id,
        expiry_seconds=func.timeout_ms // 1000,
    )
    # Test runner: always succeeds after a brief delay simulating work
    await asyncio.sleep(0.1)
    output = {
        "message": "Function executed successfully (test runner)",
        "function_id": func.id,
        "organisation_id": func.organisation_id,
    }
    await update_run_status(
        session,
        run,
        FunctionRunStatus.success,
        output=output,
    )
    # Emit audit for the completed run (D2)
    await record_audit(
        session,
        action="function.run.completed",
        obj=run,
        actor=f"function:{func.id}",
        organisation_id=func.organisation_id,
        details={
            "function_id": func.id,
            "function_version": func.updated_at.isoformat() if func.updated_at else "",
            "trigger": run.trigger,
            "sandbox_policy": "test-stub",
            "status": FunctionRunStatus.success.value,
            "duration_ms": run.duration_ms,
        },
    )


async def _process_queued_runs(session: AsyncSession) -> int:
    """Claim and execute queued function runs. Returns count processed."""
    from sqlalchemy import select

    # Check all orgs for enabled functions with queued runs
    result = await session.execute(
        select(FunctionRun)
        .join(Function, Function.id == FunctionRun.function_id)
        .where(
            FunctionRun.status == FunctionRunStatus.queued,
            Function.enabled == True,  # noqa: E712
            Function.deleted_at.is_(None),
        )
        .order_by(FunctionRun.created_at)
        .limit(10)
    )
    queued = list(result.scalars().all())
    processed = 0

    for run in queued:
        func = await session.get(Function, run.function_id)
        if func is None or not func.enabled:
            continue

        # Mark as running
        await update_run_status(session, run, FunctionRunStatus.running)
        await session.commit()

        try:
            await _execute_function(session, func, run)
        except asyncio.TimeoutError:
            await update_run_status(
                session,
                run,
                FunctionRunStatus.timeout,
                error=f"timed out after {func.timeout_ms}ms",
            )
            await record_audit(
                session,
                action="function.run.timeout",
                obj=run,
                actor=f"function:{func.id}",
                organisation_id=func.organisation_id,
                details={
                    "function_id": func.id,
                    "sandbox_policy": "test-stub",
                    "status": FunctionRunStatus.timeout.value,
                    "error": f"timed out after {func.timeout_ms}ms",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("function %d run %s failed", func.id, run.id)
            await update_run_status(
                session,
                run,
                FunctionRunStatus.failure,
                error=str(exc)[:1000],
            )
            await record_audit(
                session,
                action="function.run.failed",
                obj=run,
                actor=f"function:{func.id}",
                organisation_id=func.organisation_id,
                details={
                    "function_id": func.id,
                    "sandbox_policy": "test-stub",
                    "status": FunctionRunStatus.failure.value,
                    "error": str(exc)[:1000],
                },
            )
        await session.commit()
        processed += 1

    return processed


async def run_function_poller() -> None:
    """Background loop: drain queued function runs. Started by the app lifespan."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                count = await _process_queued_runs(session)
                if count:
                    logger.info("function runner processed %d runs", count)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — poller must never die
            logger.exception("function runner iteration failed")
        await asyncio.sleep(RUNNER_POLL_INTERVAL)
