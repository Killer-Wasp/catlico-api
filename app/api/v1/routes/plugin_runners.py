from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import SuperAdminUser
from app.core.db import get_session
from app.models.plugin_runner import (
    PluginDefinition,
    PluginRunner as PluginRunnerModel,
    PluginVersion,
    RunnerPluginInstallation,
    normalize_plugin_descriptor,
)


router = APIRouter(prefix="/plugin-runners", tags=["plugin-runners"])


async def _runner_get_json(base_url: str, path: str) -> dict | list:
    if not base_url:
        raise httpx.RequestError("runner base_url is empty")
    url = f"{base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _upsert_plugin_inventory(
    session: AsyncSession,
    runner: PluginRunnerModel,
    plugins: list[dict],
) -> None:
    now = datetime.now(UTC)
    for descriptor in plugins:
        # Runner reports a load envelope on the sync path; unwrap to the real
        # manifest and learn whether the plugin actually loaded. A failed plugin
        # is recorded but never marked installed/active, so it can't be dispatched
        # (its run would accept but never produce a result).
        manifest, loaded_ok, load_error = normalize_plugin_descriptor(descriptor)
        plugin_id = manifest["id"]
        version_str = manifest["version"]
        version_id = f"{plugin_id}@{version_str}"
        pdef = await session.get(PluginDefinition, plugin_id)
        if pdef is None:
            pdef = PluginDefinition(
                id=plugin_id,
                display_name=manifest.get("name", plugin_id),
                description=manifest.get("description", ""),
                manifest=manifest,
            )
            session.add(pdef)
        else:
            pdef.display_name = manifest.get("name", pdef.display_name)
            pdef.description = manifest.get("description", pdef.description)
            pdef.manifest = manifest
        await session.flush()

        pver = await session.get(PluginVersion, version_id)
        if pver is None:
            pver = PluginVersion(
                id=version_id,
                plugin_id=plugin_id,
                version=version_str,
                manifest=manifest,
                commit_sha=manifest.get("commit_sha", ""),
                image_digest=manifest.get("image_digest", ""),
                installed_at=now,
                status="active" if loaded_ok else "failed",
                install_log=load_error,
            )
            session.add(pver)
        else:
            pver.manifest = manifest
            pver.status = "active" if loaded_ok else "failed"
            pver.install_log = load_error
        await session.flush()
        installation = await session.get(
            RunnerPluginInstallation,
            (runner.id, version_id),
        )
        install_status = "installed" if loaded_ok else "failed"
        if installation is None:
            installation = RunnerPluginInstallation(
                runner_id=runner.id,
                plugin_version_id=version_id,
                install_status=install_status,
                health_status=manifest.get("health_status"),
                installed_at=now,
                last_seen_at=now,
            )
            session.add(installation)
        else:
            installation.install_status = install_status
            installation.health_status = manifest.get(
                "health_status",
                installation.health_status,
            )
            installation.last_seen_at = now
        # Only a plugin that loaded becomes the dispatchable active version.
        if loaded_ok:
            pdef.active_version_id = version_id
    await session.flush()


@router.get("")
async def list_runners(
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    rows = (await session.execute(select(PluginRunnerModel))).scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "base_url": r.base_url,
            "status": r.status,
            "version": r.version,
            "isolation_mode": r.isolation_mode,
            "last_health_at": r.last_health_at.isoformat() if r.last_health_at else None,
            "last_heartbeat_at": r.last_heartbeat_at.isoformat() if r.last_heartbeat_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/{runner_id}")
async def get_runner(
    runner_id: str,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    return {
        "id": runner.id,
        "name": runner.name,
        "base_url": runner.base_url,
        "status": runner.status,
        "version": runner.version,
        "isolation_mode": runner.isolation_mode,
        "last_health_at": runner.last_health_at.isoformat() if runner.last_health_at else None,
        "last_heartbeat_at": runner.last_heartbeat_at.isoformat() if runner.last_heartbeat_at else None,
        "created_at": runner.created_at.isoformat() if runner.created_at else None,
    }


@router.get("/{runner_id}/stats")
async def get_runner_stats(
    runner_id: str,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    window: str = "30d",
) -> dict:
    from app.crud import plugin_stats as stats_crud

    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    try:
        return await stats_crud.runner_stats(session, runner_id, window)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.post("/{runner_id}/health-check")
async def health_check(
    runner_id: str,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    try:
        health = await _runner_get_json(runner.base_url, "/internal/health")
    except Exception as exc:  # noqa: BLE001 - setup flow reports runner errors.
        runner.status = "unhealthy"
        runner.last_error = str(exc)
        runner.last_health_at = datetime.now(UTC)
        await session.flush()
        return {"status": runner.status, "error": runner.last_error}

    runner.status = "healthy"
    runner.version = health.get("version", runner.version)
    runner.capabilities = health.get("capabilities", runner.capabilities)
    runner.isolation_mode = health.get("isolation_mode", runner.isolation_mode)
    runner.last_error = None
    runner.last_health_at = datetime.now(UTC)
    await session.flush()
    return {"status": runner.status, "health": health}


@router.post("/{runner_id}/sync")
async def sync_runner(
    runner_id: str,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    try:
        payload = await _runner_get_json(runner.base_url, "/internal/plugins")
    except Exception as exc:  # noqa: BLE001 - expose setup failure as API error.
        runner.status = "unhealthy"
        runner.last_error = str(exc)
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Runner plugin sync failed",
        ) from exc

    plugins = payload.get("plugins", payload if isinstance(payload, list) else [])
    await _upsert_plugin_inventory(session, runner, plugins)
    runner.status = "healthy"
    runner.last_error = None
    await session.flush()
    return {"status": "synced", "plugin_count": len(plugins)}


@router.post("/{runner_id}/plugins/install")
async def install_plugin_gone(
    runner_id: str,
    _: SuperAdminUser,
) -> dict:
    """Removed (venv redesign WP4). Plugins are no longer installed at runtime:
    they are provisioned into the runner's ``/plugins`` directory at image build
    (``plugin-runner install <source>`` + rebuild/restart the runner image). This
    endpoint always returns 410 Gone.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Plugin install has been removed; plugins are provisioned at image "
            "build / via the runner's plugins dir."
        ),
    )
