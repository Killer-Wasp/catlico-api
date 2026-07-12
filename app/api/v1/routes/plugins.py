"""Public API: plugin catalog, config, enable/disable, and run views."""
import uuid
from typing import Annotated

from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from app.api.deps import ActiveOrgOrApiKeyContext
from app.core.db import get_session
from app.crud import plugin_stats as stats_crud
from app.services import plugin_audit
from app.services import plugin_circuit_breaker as circuit_breaker
from app.models.plugin_runner import (
    OrgPlugin,
    PluginConfig,
    PluginDefinition,
    PluginRunner as PluginRunnerModel,
    PluginRun,
    RunnerPluginInstallation,
)

router = APIRouter(prefix="/plugins", tags=["plugins"])
runs_router = APIRouter(prefix="/plugin-runs", tags=["plugin-runs"])


def _require(permission: str, permissions: set[str]) -> None:
    if permission not in permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing permission: {permission}",
        )


def _is_secret_param(c: dict) -> bool:
    """Manifests flag secrets as `type: "secret"` or a `secret: true` boolean."""
    return c.get("type") == "secret" or bool(c.get("secret"))


def _param_config_status(c: dict, settings: dict, secret_keys: set[str]) -> dict:
    """Server-owned per-parameter completeness for the config form gating.

    Precedence mirrors the drawer: environment source is locked-and-complete,
    otherwise an org value or a schema default satisfies the parameter; a
    required parameter with neither is `missing`.
    """
    name = c["name"]
    source = c.get("source") or "org"
    if source == "environment":
        return {"status": "configured", "source": "environment"}
    has_default = (c.get("defaultValue") is not None) or (c.get("default") is not None)
    if _is_secret_param(c):
        stored = name in secret_keys
        return {
            "status": "configured" if (stored or has_default) else "missing",
            "source": "org" if stored else ("default" if has_default else "org"),
            "secret_configured": stored,
        }
    value = settings.get(name)
    if value not in (None, "", [], {}):
        return {"status": "configured", "source": "org"}
    if has_default:
        return {"status": "configured", "source": "default"}
    return {"status": "missing", "source": "org"}


def _config_status_map(manifest: dict, cfg) -> dict[str, dict]:
    settings = dict(cfg.settings or {}) if cfg is not None else {}
    secret_keys: set[str] = set()
    if cfg is not None and cfg.secrets_encrypted:
        from app.core.crypto import decrypt_secrets
        secret_keys = set(decrypt_secrets(cfg.secrets_encrypted).keys())
    out: dict[str, dict] = {}
    for c in (manifest or {}).get("configuration", []) or []:
        if c.get("name"):
            out[c["name"]] = _param_config_status(c, settings, secret_keys)
    return out


def _config_complete(manifest: dict, cfg) -> bool:
    status_map = _config_status_map(manifest, cfg)
    for c in (manifest or {}).get("configuration", []) or []:
        if c.get("required") and status_map.get(c["name"], {}).get("status") != "configured":
            return False
    return True


def _plugin_public(
    pdef: PluginDefinition,
    org_plugin=None,
    runner_ids: list[str] | None = None,
    cfg=None,
) -> dict:
    runner_ids = runner_ids or []
    return {
        "id": pdef.id,
        "display_name": pdef.display_name,
        "description": pdef.description,
        "manifest": pdef.manifest,
        "available": pdef.available,
        "runner_id": runner_ids[0] if runner_ids else None,
        "runner_ids": runner_ids,
        "enabled": org_plugin.enabled if org_plugin else False,
        "auto_run_enabled": org_plugin.auto_run_enabled if org_plugin else False,
        "auto_apply_actions": org_plugin.auto_apply_actions if org_plugin else [],
        "config_complete": _config_complete(pdef.manifest or {}, cfg),
    }


async def _runner_resource_json(
    base_url: str,
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    if not base_url:
        raise httpx.RequestError("runner base_url is empty")
    url = f"{base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.request(method, url, json=body)
        response.raise_for_status()
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return {"body": response.text}


async def _plugin_runner_or_404(
    session: AsyncSession,
    plugin_id: str,
) -> tuple[PluginDefinition, PluginRunnerModel]:
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    if not pdef.active_version_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plugin has no active version")
    installation = (
        await session.execute(
            select(RunnerPluginInstallation).where(
                RunnerPluginInstallation.plugin_version_id == pdef.active_version_id
            )
        )
    ).scalars().first()
    if installation is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plugin runner not found")
    runner = await session.get(PluginRunnerModel, installation.runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Plugin runner not found")
    return pdef, runner


async def _runner_ids_for_definition(
    session: AsyncSession,
    pdef: PluginDefinition,
) -> list[str]:
    if not pdef.active_version_id:
        return []
    rows = (
        await session.execute(
            select(RunnerPluginInstallation).where(
                RunnerPluginInstallation.plugin_version_id == pdef.active_version_id
            )
        )
    ).scalars().all()
    return [row.runner_id for row in rows]


def _plugin_run_public(run: PluginRun) -> dict:
    return {
        "id": str(run.id),
        "event_id": run.event_id,
        "event_type": run.event_type,
        "organisation_id": run.organisation_id,
        "plugin_id": run.plugin_id,
        "plugin_version_id": run.plugin_version_id,
        "runner_id": run.runner_id,
        "event_object_type": run.event_object_type,
        "event_object_id": run.event_object_id,
        "status": run.status,
        "skip_reason": run.skip_reason,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "error": run.error,
        "result_summary": run.result_summary,
        "operation_count": run.operation_count,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _manual_run_view(envelope: dict, pdef: PluginDefinition, runner_id: str) -> dict:
    """Public view of a just-queued manual run. The run row itself is created by
    the runner when it claims the synthesized event, so this is a synthetic view
    keyed by the deterministic event_id (a stable id across duplicate submits)."""
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, envelope["event_id"])),
        "event_id": envelope["event_id"],
        "event_type": envelope["event_type"],
        "organisation_id": envelope["organisation_id"],
        "plugin_id": pdef.id,
        "plugin_version_id": pdef.active_version_id,
        "runner_id": runner_id,
        "event_object_type": envelope["object"]["type"],
        "event_object_id": envelope["object"]["id"],
        "status": "queued",
        "skip_reason": None,
        "started_at": None,
        "ended_at": None,
        "error": None,
        "result_summary": None,
        "operation_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
    }


async def create_manual_plugin_run(
    session: AsyncSession,
    ctx: ActiveOrgOrApiKeyContext,
    *,
    plugin_id: str,
    entity_type: str,
    entity_id: str,
) -> dict:
    """Route an on-demand manual run onto the shared event/delivery path.

    Rather than inserting a ``queued`` run nothing ever executes (the old
    scaffold), we synthesize a manual envelope and enqueue a delivery per healthy
    runner. The runner claims the run through the normal path; ``create_run``
    reads the stored envelope's ``manual`` flag server-side and grants the
    analyst-intent relaxations (no trigger match required, no auto-run required,
    freshness cache bypassed) while keeping TLP/PAP and concurrency guards.
    """
    _require("run:enrichment", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None or not org_plugin.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin is not enabled for this organisation",
        )
    if not pdef.active_version_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin has no active version",
        )
    installation = (
        await session.execute(
            select(RunnerPluginInstallation).where(
                RunnerPluginInstallation.plugin_version_id == pdef.active_version_id,
                RunnerPluginInstallation.install_status == "installed",
            )
        )
    ).scalars().first()
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin is not installed on a runner",
        )

    if entity_type != "observable":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Manual plugin runs currently support observable entities",
        )
    # The same visibility resolution the observable read routes use — a bare
    # get_observable would let any org trigger runs (and thus enrichment spend +
    # result writes) against another org's observable by guessing its UUID.
    from app.api.v1.routes.observables import _resolve_observable_visibility

    _, _, effective_perms = await _resolve_observable_visibility(
        session, ctx, uuid.UUID(entity_id)
    )
    _require("read:observable", effective_perms)

    from app.services.plugin_dispatch import (
        _enqueue_for_healthy_runners,
        build_manual_envelope,
    )

    envelope = build_manual_envelope(
        org_id=ctx.organisation_id,
        plugin_id=plugin_id,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=f"user:{ctx.user.id}",
    )
    await _enqueue_for_healthy_runners(session, envelope)
    await session.flush()
    return _manual_run_view(envelope, pdef, installation.runner_id)


# --- Plugin catalog ---


@router.get("")
async def list_plugins(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    _require("read:connector", ctx.permissions)
    result = await session.execute(select(PluginDefinition))
    pdefs = result.scalars().all()
    org_result = await session.execute(
        select(OrgPlugin).where(
            OrgPlugin.organisation_id == ctx.organisation_id,
            OrgPlugin.enabled.is_(True),
        )
    )
    enabled_map = {op.plugin_id: op for op in org_result.scalars().all()}
    cfg_result = await session.execute(
        select(PluginConfig).where(PluginConfig.organisation_id == ctx.organisation_id)
    )
    cfg_map = {c.plugin_id: c for c in cfg_result.scalars().all()}
    return [
        _plugin_public(
            p,
            enabled_map.get(p.id),
            await _runner_ids_for_definition(session, p),
            cfg_map.get(p.id),
        )
        for p in pdefs
    ]


@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("read:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    cfg = await session.get(PluginConfig, (ctx.organisation_id, plugin_id))
    return _plugin_public(
        pdef,
        org_plugin,
        await _runner_ids_for_definition(session, pdef),
        cfg,
    )


@router.get("/{plugin_id}/stats")
async def get_plugin_stats(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: str = "30d",
) -> dict:
    _require("read:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    try:
        return await stats_crud.plugin_stats(
            session, ctx.organisation_id, plugin_id, window
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.get("/{plugin_id}/resources/{resource_path:path}")
async def get_plugin_resource(
    plugin_id: str,
    resource_path: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("read:connector", ctx.permissions)
    _, runner = await _plugin_runner_or_404(session, plugin_id)
    try:
        return await _runner_resource_json(
            runner.base_url,
            "GET",
            f"/internal/plugins/{plugin_id}/resources/{resource_path}",
        )
    except Exception as exc:  # noqa: BLE001 - proxy returns a stable gateway error.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Plugin resource request failed",
        ) from exc


@router.post("/{plugin_id}/resources/{resource_path:path}")
async def post_plugin_resource(
    plugin_id: str,
    resource_path: str,
    body: dict,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    _, runner = await _plugin_runner_or_404(session, plugin_id)
    try:
        return await _runner_resource_json(
            runner.base_url,
            "POST",
            f"/internal/plugins/{plugin_id}/resources/{resource_path}",
            body,
        )
    except Exception as exc:  # noqa: BLE001 - proxy returns a stable gateway error.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Plugin resource request failed",
        ) from exc


@router.post("/{plugin_id}/run")
async def run_plugin(
    plugin_id: str,
    body: dict,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return await create_manual_plugin_run(
        session,
        ctx,
        plugin_id=plugin_id,
        entity_type=body.get("entity_type", ""),
        entity_id=str(body.get("entity_id", "")),
    )


# --- Enable / Disable ---


@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None:
        org_plugin = OrgPlugin(
            organisation_id=ctx.organisation_id,
            plugin_id=plugin_id,
            enabled=True,
        )
        session.add(org_plugin)
    else:
        org_plugin.enabled = True
    await session.flush()
    await plugin_audit.record_admin_action(
        session, action="enable", object_type="plugin", object_id=plugin_id,
        actor=str(ctx.user.id), organisation_id=ctx.organisation_id,
    )
    return {"enabled": True}


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None:
        return {"enabled": False}
    org_plugin.enabled = False
    await session.flush()
    await plugin_audit.record_admin_action(
        session, action="disable", object_type="plugin", object_id=plugin_id,
        actor=str(ctx.user.id), organisation_id=ctx.organisation_id,
    )
    return {"enabled": False}


# --- Auto-run ---


@router.post("/{plugin_id}/auto-run/enable")
async def enable_auto_run(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None or not org_plugin.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin must be enabled before auto-run can be enabled.",
        )
    org_plugin.auto_run_enabled = True
    await session.flush()
    await plugin_audit.record_admin_action(
        session, action="auto_run_enable", object_type="plugin", object_id=plugin_id,
        actor=str(ctx.user.id), organisation_id=ctx.organisation_id,
    )
    return {"auto_run_enabled": True}


@router.post("/{plugin_id}/auto-run/disable")
async def disable_auto_run(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None:
        return {"auto_run_enabled": False}
    org_plugin.auto_run_enabled = False
    await session.flush()
    await plugin_audit.record_admin_action(
        session, action="auto_run_disable", object_type="plugin", object_id=plugin_id,
        actor=str(ctx.user.id), organisation_id=ctx.organisation_id,
    )
    return {"auto_run_enabled": False}


@router.put("/{plugin_id}/auto-apply")
async def set_auto_apply_policy(
    plugin_id: str,
    body: dict,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Set which low-risk proposed-action types this org auto-applies (no approval)."""
    _require("write:connector", ctx.permissions)
    from app.crud.plugin_proposed_action import LOW_RISK_ACTIONS

    requested = body.get("actions", [])
    if not isinstance(requested, list):
        raise HTTPException(status_code=422, detail="actions must be a list")
    invalid = set(requested) - LOW_RISK_ACTIONS
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Not auto-appliable: {sorted(invalid)}",
        )
    org_plugin = await session.get(OrgPlugin, (ctx.organisation_id, plugin_id))
    if org_plugin is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Enable the plugin before setting an auto-apply policy.",
        )
    org_plugin.auto_apply_actions = list(requested)
    await session.flush()
    await plugin_audit.record_admin_action(
        session, action="auto_apply_policy", object_type="plugin", object_id=plugin_id,
        actor=str(ctx.user.id), organisation_id=ctx.organisation_id,
        details={"actions": list(requested)},
    )
    return {"auto_apply_actions": org_plugin.auto_apply_actions}


# --- Config ---


@router.put("/{plugin_id}/config")
async def set_plugin_config(
    plugin_id: str,
    body: dict,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    org_id = ctx.organisation_id
    cfg = await session.get(PluginConfig, (org_id, plugin_id))
    old_settings = dict(cfg.settings) if cfg is not None else {}
    if cfg is None:
        cfg = PluginConfig(organisation_id=org_id, plugin_id=plugin_id)
        session.add(cfg)

    new_settings = body.get("settings", {})
    cfg.settings = new_settings
    cfg.updated_by = str(ctx.user.id)
    secrets = body.get("secrets", {})
    if secrets:
        # Secret contract: absent key keeps, string replaces, null deletes. Merge
        # onto the stored set rather than overwriting, or a single-field update
        # would silently drop every other stored secret.
        from app.core.crypto import decrypt_secrets, encrypt_secrets
        merged = decrypt_secrets(cfg.secrets_encrypted)
        for key, value in secrets.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        cfg.secrets_encrypted = encrypt_secrets(merged)
    await session.flush()

    audit_details = plugin_audit.summarize_config_change(
        pdef.manifest or {}, old_settings, new_settings, set(secrets.keys())
    )
    await plugin_audit.record_admin_action(
        session, action="config_update", object_type="plugin_config",
        object_id=plugin_id, actor=str(ctx.user.id), organisation_id=org_id,
        details=audit_details,
    )

    return {
        "settings": cfg.settings,
        "has_secrets": bool(cfg.secrets_encrypted),
    }


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Current org-scoped settings for the config drawer. Secrets are never
    returned in the clear — only whether any are stored."""
    _require("read:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    cfg = await session.get(PluginConfig, (ctx.organisation_id, plugin_id))
    if cfg is None:
        return {"settings": {}, "has_secrets": False}
    return {
        "settings": dict(cfg.settings or {}),
        "has_secrets": bool(cfg.secrets_encrypted),
    }


@router.get("/{plugin_id}/config/status")
async def get_plugin_config_status(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Per-parameter completeness the config form gates on (never recomputed
    client-side, so enable-gating cannot drift from the enable-time check)."""
    _require("read:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    cfg = await session.get(PluginConfig, (ctx.organisation_id, plugin_id))
    return _config_status_map(pdef.manifest or {}, cfg)


@router.post("/{plugin_id}/config/test")
async def test_plugin_config(
    plugin_id: str,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")

    # Check manifest for required secrets
    manifest = pdef.manifest or {}
    required_secrets = [
        c["name"]
        for c in manifest.get("configuration", [])
        if _is_secret_param(c) and c.get("required")
    ]
    if required_secrets:
        org_id = ctx.organisation_id
        cfg = await session.get(PluginConfig, (org_id, plugin_id))
        if cfg is None or not cfg.secrets_encrypted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"missing": required_secrets},
            )
    # A passing config test is the admin's "config is fixed" signal: clear any
    # auto-suspension so the circuit breaker re-enables dispatch.
    await circuit_breaker.clear_suspension(session, ctx.organisation_id, plugin_id)
    return {"ok": True, "message": "Plugin configuration is valid."}


# --- Plugin runs ---


@runs_router.get("")
async def list_plugin_runs(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    _require("read:connector", ctx.permissions)
    result = await session.execute(
        select(PluginRun)
        .where(PluginRun.organisation_id == ctx.organisation_id)
        .order_by(PluginRun.created_at.desc())
        .limit(100)
    )
    runs = result.scalars().all()
    return [
        _plugin_run_public(r)
        for r in runs
    ]


@runs_router.get("/{run_id}")
async def get_plugin_run(
    run_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("read:connector", ctx.permissions)
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.organisation_id != ctx.organisation_id and not ctx.user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return _plugin_run_public(run)


@runs_router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    # Enforce org-scoped access
    if run.organisation_id != ctx.organisation_id and not ctx.user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.status = "cancelled"
    run.ended_at = datetime.now(UTC)
    await session.flush()
    return {"status": run.status}


@runs_router.post("/retry-failed")
async def retry_failed(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    _require("write:connector", ctx.permissions)
    from app.services.plugin_dispatch import redeliver_run

    result = await session.execute(
        select(PluginRun).where(
            PluginRun.organisation_id == ctx.organisation_id,
            PluginRun.status == "failure",
        )
    )
    failed = result.scalars().all()
    now = datetime.now(UTC)
    redispatched = 0
    for run in failed:
        # Reuse the row (do not insert a second): reset to queued, bump attempt,
        # clear terminal fields, invalidate the stale runtime token.
        run.status = "queued"
        run.attempt += 1
        run.error = None
        run.error_kind = None
        run.skip_reason = None
        run.started_at = None
        run.ended_at = None
        run.runtime_token_hash = None
        run.runtime_token_expires_at = None
        # Reset/resend the delivery so the push loop actually re-sends it (a bare
        # re-enqueue would be skipped as already-delivered).
        await redeliver_run(session, run, now)
        redispatched += 1
    await session.flush()
    return {"retried": redispatched, "redispatched": redispatched}


@runs_router.post("/clear-finished")
async def clear_finished_runs(
    ctx: ActiveOrgOrApiKeyContext,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Remove the org's terminal runs (success/failure/timeout/cancelled/skipped)
    from the queue. Results/files SET NULL, proposed actions CASCADE (see FKs)."""
    _require("write:connector", ctx.permissions)
    terminal = ("success", "failure", "timeout", "cancelled", "skipped")
    count_result = await session.execute(
        select(PluginRun.id).where(
            PluginRun.organisation_id == ctx.organisation_id,
            PluginRun.status.in_(terminal),
        )
    )
    ids = count_result.scalars().all()
    if ids:
        await session.execute(
            delete(PluginRun).where(PluginRun.id.in_(ids))
        )
        await session.flush()
    return {"cleared": len(ids)}
