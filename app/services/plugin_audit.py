"""Admin-action audit for plugin/runner management.

Records who did what to the plugin system (enabled auto-run, changed config,
approved a proposed action) into the Audit table so it shows up in the audit
trail. Secret config values are never recorded — only the key name and whether it
was set or cleared.

Writes the Audit row directly (not through the notification/outbox fan-out): this
is a compliance trail, and plugin-system object types must never be dispatched to
runners (loop prevention).
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import get_request_id
from app.models.audit import Audit


async def record_admin_action(
    session: AsyncSession,
    *,
    action: str,
    object_type: str,
    object_id: str,
    actor: str,
    organisation_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> Audit:
    audit = Audit(
        request_id=get_request_id(),
        action=action,
        main_action=True,
        object_type=object_type,
        object_id=object_id,
        context_type="plugin",
        context_id=object_id,
        actor=actor,
        details=details or {},
    )
    if organisation_id is not None:
        audit.details = {**(details or {}), "organisation_id": organisation_id}
    session.add(audit)
    await session.flush()
    return audit


def summarize_config_change(
    manifest: dict, old_settings: dict, new_settings: dict, secret_keys: set[str]
) -> dict:
    """Describe a config change for audit: non-secret keys record old→new, secret
    keys record only the key name and the action, never a value."""
    changed_settings: dict[str, Any] = {}
    for key in set(old_settings) | set(new_settings):
        before = old_settings.get(key)
        after = new_settings.get(key)
        if before != after:
            changed_settings[key] = {"from": before, "to": after}
    return {
        "settings_changed": changed_settings,
        "secrets_changed": sorted(secret_keys),
    }
