"""Config circuit breaker for the plugin system (Phase 4).

A plugin whose per-org configuration is broken (missing/invalid secret, bad
endpoint) fails every run with ``error_kind == "config"`` — retrying it forever
just burns runner capacity and floods the run log. The breaker counts consecutive
config failures per ``(organisation, plugin)`` and, at a threshold, sets
``OrgPlugin.suspended_reason``. The dispatcher already skips suspended plugins
(``plugin_dispatch``), so tripping the breaker stops auto-dispatch until the admin
fixes the config and a config test clears the suspension.

Only ``config`` failures count. A ``timeout``/``transient``/``unknown`` failure is
not a config problem and leaves the streak untouched; any *successful* run proves
the config is currently good and resets the streak.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plugin_runner import OrgPlugin, PluginRun


async def record_run_outcome(
    session: AsyncSession, run: PluginRun, *, threshold: int
) -> None:
    """Update the config-failure breaker for ``run``'s org+plugin from a terminal
    run. Call after the run's ``status``/``error_kind`` are set. No-op if the org
    never enabled the plugin (no ``OrgPlugin`` row)."""
    op = await session.get(OrgPlugin, (run.organisation_id, run.plugin_id))
    if op is None:
        return

    if run.status == "success":
        op.config_failure_streak = 0
        return

    if run.status == "failure" and run.error_kind == "config":
        op.config_failure_streak += 1
        if op.config_failure_streak >= threshold and op.suspended_reason is None:
            op.suspended_reason = (
                f"Auto-suspended after {op.config_failure_streak} consecutive "
                "configuration failures. Fix the configuration and run a config "
                "test to re-enable."
            )
    # Any other terminal status (timeout / cancelled / skipped / non-config
    # failure) is not evidence about config, so the streak is left unchanged.


async def clear_suspension(
    session: AsyncSession, organisation_id: str, plugin_id: str
) -> None:
    """Clear an auto-suspension and reset the streak — called when a config test
    passes (the admin's explicit "config is fixed" signal)."""
    op = await session.get(OrgPlugin, (organisation_id, plugin_id))
    if op is None:
        return
    op.config_failure_streak = 0
    op.suspended_reason = None
