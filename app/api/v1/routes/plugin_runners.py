import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import SuperAdminUser
from app.core.configs import settings
from app.core.crypto import decrypt_string
from app.core.db import get_session
from app.models.plugin_runner import (
    PluginDefinition,
    PluginInstallRequest,
    PluginRunner as PluginRunnerModel,
    PluginVersion,
    RunnerPluginInstallation,
)
# Reuse the exact HMAC scheme the event-push loop uses so the runner verifies
# install triggers the same way it verifies /internal/events pushes.
from app.services.plugin_dispatch import _signature


router = APIRouter(prefix="/plugin-runners", tags=["plugin-runners"])


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_enrollment_token() -> str:
    return f"cpe_{secrets.token_urlsafe(32)}"


async def _runner_get_json(base_url: str, path: str) -> dict | list:
    if not base_url:
        raise httpx.RequestError("runner base_url is empty")
    url = f"{base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def _runner_post_signed(
    runner: PluginRunnerModel,
    path: str,
    payload: dict,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict | list:
    """POST a JSON body to a runner host, HMAC-signed with the runner's push
    signing secret. Signs the raw body with the SAME scheme/header the event
    push uses (``x-catlico-signature``) so the runner verifies it identically.

    Raises ``httpx.HTTPError`` on connection failure or a non-2xx response; the
    caller maps that to ``502 BAD_GATEWAY`` (mirrors ``sync_runner``). The
    ``transport`` kwarg exists only for tests.
    """
    if not runner.base_url:
        raise httpx.RequestError("runner base_url is empty")
    secret = decrypt_string(runner.push_signing_secret_encrypted)
    if not secret:
        raise httpx.RequestError("runner push signing secret is unavailable")
    body = json.dumps(payload).encode()
    headers = {
        "content-type": "application/json",
        "x-catlico-signature": _signature(body, secret),
    }
    url = f"{runner.base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
        response = await client.post(url, content=body, headers=headers)
        response.raise_for_status()
        return response.json()


async def _upsert_plugin_inventory(
    session: AsyncSession,
    runner: PluginRunnerModel,
    plugins: list[dict],
) -> None:
    now = datetime.now(UTC)
    for manifest in plugins:
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
                status="active",
            )
            session.add(pver)
        else:
            pver.manifest = manifest
            pver.status = "active"
        await session.flush()
        installation = await session.get(
            RunnerPluginInstallation,
            (runner.id, version_id),
        )
        if installation is None:
            installation = RunnerPluginInstallation(
                runner_id=runner.id,
                plugin_version_id=version_id,
                install_status="installed",
                health_status=manifest.get("health_status"),
                installed_at=now,
                last_seen_at=now,
            )
            session.add(installation)
        else:
            installation.install_status = "installed"
            installation.health_status = manifest.get(
                "health_status",
                installation.health_status,
            )
            installation.last_seen_at = now
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


@router.post("")
async def create_runner(
    body: dict,
    user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    token = _new_enrollment_token()
    expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.PLUGIN_RUNNER_ENROLLMENT_TOKEN_TTL_SECONDS
    )
    runner = await session.get(PluginRunnerModel, body["id"])
    if runner is None:
        runner = PluginRunnerModel(
            id=body["id"],
            name=body.get("name", ""),
            base_url=body.get("base_url", ""),
            status="unhealthy",
            enrollment_state="pending",
            enrollment_token_hash=_hash_secret(token),
            enrollment_token_expires_at=expires_at,
            created_by=str(user.id),
        )
        session.add(runner)
    else:
        runner.name = body.get("name", runner.name)
        runner.base_url = body.get("base_url", runner.base_url)
        runner.enrollment_state = "pending"
        runner.enrollment_token_hash = _hash_secret(token)
        runner.enrollment_token_expires_at = expires_at
    await session.flush()
    return {
        "id": runner.id,
        "name": runner.name,
        "status": runner.status,
        "enrollment_state": runner.enrollment_state,
        "enrollment_token": token,
        "enrollment_token_expires_at": expires_at.isoformat(),
    }


@router.post("/{runner_id}/re-enroll")
async def re_enroll_runner(
    runner_id: str,
    _: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Mint a fresh one-time enrollment token for an existing runner.

    Resets the runner to ``pending`` so the runner can exchange the new token
    for machine credentials again (used after a lost credential or an admin
    reset). The old enrollment token, if any, is superseded. Machine
    credentials are only issued on the runner-side ``/register`` exchange, so
    this route never returns them.
    """
    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    token = _new_enrollment_token()
    expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.PLUGIN_RUNNER_ENROLLMENT_TOKEN_TTL_SECONDS
    )
    runner.enrollment_state = "pending"
    runner.enrollment_token_hash = _hash_secret(token)
    runner.enrollment_token_expires_at = expires_at
    await session.flush()
    return {
        "id": runner.id,
        "name": runner.name,
        "status": runner.status,
        "enrollment_state": runner.enrollment_state,
        "enrollment_token": token,
        "enrollment_token_expires_at": expires_at.isoformat(),
    }


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


@router.post(
    "/{runner_id}/plugins/install",
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_plugin(
    runner_id: str,
    body: PluginInstallRequest,
    user: SuperAdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Trigger a plugin install on a runner from a git source.

    Records the catalog rows in an *installing/pending* state, then asks the
    runner host to clone/build the plugin. The runner reports progress back via
    the internal install-status sink; this route does not wait for completion.
    """
    runner = await session.get(PluginRunnerModel, runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")

    now = datetime.now(UTC)

    # 1. Upsert the plugin definition (name = plugin_id unless already known).
    pdef = await session.get(PluginDefinition, body.plugin_id)
    if pdef is None:
        pdef = PluginDefinition(
            id=body.plugin_id,
            display_name=body.plugin_id,
            description="",
        )
        session.add(pdef)
    await session.flush()

    # 2. Create-or-get the version. Its PK is "{plugin_id}@{version}"; the
    #    version placeholder falls back to source_ref until the runner pins a
    #    real commit/tag. An existing row is reset to the installing state.
    version_str = body.version or body.source_ref or "unknown"
    version_id = f"{body.plugin_id}@{version_str}"
    pver = await session.get(PluginVersion, version_id)
    if pver is None:
        pver = PluginVersion(
            id=version_id,
            plugin_id=body.plugin_id,
            version=version_str,
            source_type="github",
            source_url=body.source_url,
            source_ref=body.source_ref,
            commit_sha="",
            status="installing",
            installed_at=now,
        )
        session.add(pver)
    else:
        pver.source_type = "github"
        pver.source_url = body.source_url
        pver.source_ref = body.source_ref
        pver.commit_sha = ""
        pver.status = "installing"
        pver.install_log = None
    await session.flush()

    # 3. Upsert the runner installation row in the pending state.
    installation = await session.get(
        RunnerPluginInstallation,
        (runner_id, version_id),
    )
    if installation is None:
        installation = RunnerPluginInstallation(
            runner_id=runner_id,
            plugin_version_id=version_id,
            install_status="pending",
            created_by=str(user.id),
        )
        session.add(installation)
    else:
        installation.install_status = "pending"
    await session.flush()

    # 4. Ask the runner host to perform the install (HMAC-signed).
    try:
        await _runner_post_signed(
            runner,
            "/internal/plugins/install",
            {
                "plugin_version_id": version_id,
                "plugin_id": body.plugin_id,
                "source_url": body.source_url,
                "source_ref": body.source_ref,
            },
        )
    except Exception as exc:  # noqa: BLE001 - surface runner failure as API error.
        runner.last_error = str(exc)
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Runner install trigger failed",
        ) from exc

    return {"plugin_version_id": version_id, "status": "installing"}
