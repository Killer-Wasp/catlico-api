"""Plugin runner internal endpoints — runner registration, heartbeat, sync,
and run lifecycle."""
import hashlib
import secrets
import uuid
from typing import Annotated

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import PluginRunner
from app.core.configs import settings
from app.core.db import get_session
from app.crud import alert as alert_crud
from app.crud import case_ as case_crud
from app.crud import observable as obs_crud
from app.services import plugin_circuit_breaker as circuit_breaker
from app.models.plugin_runner import (
    PluginRunner as PluginRunnerModel,
    PluginRunnerHeartbeat,
    PluginRunnerRegister,
    PluginDefinition,
    PluginEventDelivery,
    PluginInstallStatusUpdate,
    PluginResult,
    PluginVersion,
    RunnerPluginInstallation,
    PluginRun,
    PluginConfig,
    OrgPlugin,
)

# Install-pipeline states the runner reports; anything not terminal keeps the
# version in the "installing" bucket.
_INSTALL_PIPELINE_STATES = frozenset(
    {"cloning", "validating", "building", "health_checking", "installed", "failed"}
)

_ACTIVE_RUN_STATUSES = ("queued", "accepted", "running", "cancelling")

router = APIRouter(prefix="/plugin-runner", tags=["plugin-runner"])


def _hash_runtime_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_secret(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_runner_credential() -> str:
    return f"cpr_{secrets.token_urlsafe(32)}"


def _new_push_signing_secret() -> str:
    return f"cps_{secrets.token_urlsafe(32)}"


def _event_object_from_body(body: dict) -> tuple[str | None, str | None]:
    event_object = body.get("event_object") or {}
    object_type = body.get("event_object_type") or event_object.get("type")
    object_id = body.get("event_object_id") or event_object.get("id")
    if object_id is not None:
        object_id = str(object_id)
    return object_type, object_id


@router.post("/register")
async def register_runner(
    body: PluginRunnerRegister,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Exchange a one-time enrollment token for runner machine credentials."""
    now = datetime.now(UTC)
    if not body.enrollment_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid plugin runner enrollment token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    existing = await session.get(PluginRunnerModel, body.id)
    token_expires_at = existing.enrollment_token_expires_at if existing else None
    if token_expires_at and token_expires_at.tzinfo is None:
        token_expires_at = token_expires_at.replace(tzinfo=UTC)
    if (
        existing is None
        or existing.enrollment_state != "pending"
        or not existing.enrollment_token_hash
        or not secrets.compare_digest(
            existing.enrollment_token_hash,
            _hash_secret(body.enrollment_token),
        )
        or token_expires_at is None
        or token_expires_at < now
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid plugin runner enrollment token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    runner_credential = _new_runner_credential()
    push_signing_secret = _new_push_signing_secret()
    existing.name = body.name or existing.name
    existing.version = body.version
    existing.capabilities = body.capabilities
    existing.isolation_mode = body.isolation_mode
    existing.status = "healthy"
    existing.enrollment_state = "enrolled"
    existing.credential_hash = _hash_secret(runner_credential)
    from app.core.crypto import encrypt_string

    existing.push_signing_secret_encrypted = encrypt_string(push_signing_secret)
    existing.enrollment_token_hash = None
    existing.enrollment_token_expires_at = None
    await session.flush()

    # Upsert plugin definitions and versions from reported manifests
    for manifest in body.plugins:
        plugin_id = manifest["id"]
        version_str = manifest["version"]
        version_id = f"{plugin_id}@{version_str}"

        # 1. Upsert definition (without active_version_id on first insert)
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

        # 2. Upsert version
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

        # 3. Record that this runner hosts the version.
        installation = await session.get(
            RunnerPluginInstallation,
            (body.id, version_id),
        )
        if installation is None:
            installation = RunnerPluginInstallation(
                runner_id=body.id,
                plugin_version_id=version_id,
                install_status="installed",
                health_status=manifest.get("health_status"),
                installed_at=now,
                last_seen_at=now,
            )
            session.add(installation)
        else:
            installation.install_status = "installed"
            installation.health_status = manifest.get("health_status", installation.health_status)
            installation.last_seen_at = now

        # 4. Link active version
        pdef.active_version_id = version_id

    await session.flush()

    return {
        "id": existing.id,
        "name": existing.name,
        "status": existing.status,
        "version": existing.version,
        "runner_credential": runner_credential,
        "push_signing_secret": push_signing_secret,
    }


@router.post("/plugins/{plugin_version_id:path}/install-status")
async def report_install_status(
    plugin_version_id: str,
    body: PluginInstallStatusUpdate,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Runner-reported install progress for a plugin version it is installing.

    Idempotent. The calling runner must OWN the installation
    (``RunnerPluginInstallation`` for ``(principal.runner_id, plugin_version_id)``
    must exist) or the version is treated as not found — a runner cannot report
    status for a version it does not host.
    """
    installation = await session.get(
        RunnerPluginInstallation,
        (principal.runner_id, plugin_version_id),
    )
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin installation not found for this runner",
        )
    if body.state not in _INSTALL_PIPELINE_STATES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown install state: {body.state}",
        )

    installation.install_status = body.state

    pver = await session.get(PluginVersion, plugin_version_id)
    if pver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin version not found",
        )
    if body.state == "installed":
        pver.status = "installed"
        pver.installed_at = datetime.now(UTC)
        if body.commit_sha:
            pver.commit_sha = body.commit_sha
        if body.image_digest:
            pver.image_digest = body.image_digest
    elif body.state == "failed":
        pver.status = "failed"
    else:
        pver.status = "installing"

    # Replace the stored log; on failure fold the error text in so it is visible.
    if body.error:
        pver.install_log = (
            f"{body.install_log}\n{body.error}" if body.install_log else body.error
        )
    elif body.install_log is not None:
        pver.install_log = body.install_log

    await session.flush()
    return {"ok": True, "status": pver.status}


@router.post("/heartbeat")
async def heartbeat(
    body: PluginRunnerHeartbeat,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Runner liveness ping. Updates heartbeat timestamp."""
    if principal.runner_id != body.runner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runner credential does not match requested runner",
        )
    runner = await session.get(PluginRunnerModel, body.runner_id)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Runner not found")
    runner.last_heartbeat_at = datetime.now(UTC)
    runner.status = "healthy"
    await session.flush()
    return {"status": runner.status}


@router.get("/sync")
async def sync(
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Return active plugin versions and org enablements.
    Called by runner after heartbeat or startup. Secrets are never included."""
    # Active plugins: definitions with an active_version_id set
    result = await session.execute(
        select(PluginDefinition).where(PluginDefinition.active_version_id.isnot(None))
    )
    definitions = result.scalars().all()
    active_plugins: list[dict] = []
    for pdef in definitions:
        pver = await session.get(PluginVersion, pdef.active_version_id) if pdef.active_version_id else None
        installation_rows = []
        if pver is not None:
            installation_rows = (
                (
                    await session.execute(
                        select(RunnerPluginInstallation).where(
                            RunnerPluginInstallation.plugin_version_id == pver.id,
                            RunnerPluginInstallation.runner_id == principal.runner_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        runner_ids = [row.runner_id for row in installation_rows]
        active_plugins.append({
            "id": pdef.id,
            "name": pdef.display_name,
            "version": pver.version if pver else "",
            "manifest": pdef.manifest,
            "runner_id": runner_ids[0] if runner_ids else None,
            "runner_ids": runner_ids,
        })
    enablements = (
        (await session.execute(select(OrgPlugin)))
        .scalars()
        .all()
    )
    org_enablements = [
        {
            "organisation_id": row.organisation_id,
            "plugin_id": row.plugin_id,
            "enabled": row.enabled,
            "auto_run_enabled": row.auto_run_enabled,
            "trigger_overrides": row.trigger_overrides,
        }
        for row in enablements
    ]
    return {"active_plugins": active_plugins, "org_enablements": org_enablements}


# --- Run lifecycle ---


@router.post("/runs")
async def create_run(
    body: dict,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Create a PluginRun row. Called by runner when it decides an enabled plugin
    should receive an event. Returns run id for subsequent lifecycle calls."""
    plugin_id = body["plugin_id"]
    runner_id = body["runner_id"]
    if principal.runner_id != runner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runner credential does not match requested runner",
        )
    organisation_id = body["organisation_id"]
    event_type = body["event_type"]
    pdef = await session.get(PluginDefinition, plugin_id)
    if pdef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    plugin_version_id = f"{plugin_id}@{body['plugin_version']}"
    if pdef.active_version_id != plugin_version_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin version is not active",
        )
    pver = await session.get(PluginVersion, plugin_version_id)
    if pver is None or pver.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin version is not active",
        )
    installation = await session.get(
        RunnerPluginInstallation,
        (runner_id, plugin_version_id),
    )
    if installation is None or installation.install_status != "installed":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Runner does not host this plugin version",
        )
    manifest = pver.manifest or {}

    # Manual-ness is authoritative ONLY when the API itself marked this event
    # manual. We never trust the runner-supplied claim body for it: honouring a
    # runner-asserted ``manual`` flag would let a compromised or buggy runner
    # bypass the auto-run gate, the trigger guard, and the freshness cache for
    # any plugin — a privilege escalation across the trust boundary. Instead we
    # read it from the delivery envelope the API wrote for this event_id, and
    # only when that envelope also targets *this* plugin.
    stored_delivery = (
        await session.execute(
            select(PluginEventDelivery)
            .where(PluginEventDelivery.event_id == body["event_id"])
            .limit(1)
        )
    ).scalars().first()
    stored_envelope = stored_delivery.envelope if stored_delivery else {}
    is_manual = bool(stored_envelope.get("manual")) and (
        stored_envelope.get("target_plugin_id") == plugin_id
    )

    # Trigger guard: a manual run targets one plugin explicitly, so analyst
    # intent overrides the plugin's declared triggers.
    if not is_manual and event_type not in set(manifest.get("triggers", [])):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin does not declare this event trigger",
        )

    org_plugin = await session.get(OrgPlugin, (organisation_id, plugin_id))
    # Manual runs require the plugin to be enabled (mirroring the public run
    # endpoint) but NOT auto-run enabled — analyst intent, not automation policy.
    if org_plugin is None or not org_plugin.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin is not enabled for auto-run in this organisation",
        )
    if not is_manual and not org_plugin.auto_run_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Plugin is not enabled for auto-run in this organisation",
        )

    # Claim / retry arbitration. The (event_id, plugin_id) unique constraint is
    # the multi-runner arbiter: a second runner claiming a *live* run must still
    # lose (409). But a run this same runner already owns that has been re-queued
    # — a Task-B retry, or a manual run awaiting first pickup — is meant to be
    # reused: same row, re-minted token, no second insert and no double-run.
    existing_run = (
        await session.execute(
            select(PluginRun).where(
                PluginRun.event_id == body["event_id"],
                PluginRun.plugin_id == plugin_id,
            )
        )
    ).scalar_one_or_none()
    if existing_run is not None:
        # "Retryable" == queued AND owned by the claiming runner. Any active
        # (accepted/running/cancelling) or terminal status, or a different
        # runner, still 409s: that preserves the arbiter and blocks double-runs.
        reusable = (
            existing_run.status == "queued" and existing_run.runner_id == runner_id
        )
        if not reusable:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Plugin run already exists for this event and plugin",
                    "existing_run_id": str(existing_run.id),
                },
            )
        reuse_now = datetime.now(UTC)
        runtime_token = secrets.token_urlsafe(32)
        # attempt is owned by whoever re-queued the row (retry-failed, or the
        # up-front manual enqueue); we do NOT bump it again here or a single
        # retry would count twice.
        existing_run.runtime_token_hash = _hash_runtime_token(runtime_token)
        existing_run.runtime_token_expires_at = reuse_now + timedelta(
            seconds=settings.PLUGIN_RUNTIME_TOKEN_TTL_SECONDS
        )
        existing_run.status = "queued"
        await session.flush()
        return {
            "run_id": str(existing_run.id),
            "status": existing_run.status,
            "runtime_token": runtime_token,
        }

    event_object_type, event_object_id = _event_object_from_body(body)
    now = datetime.now(UTC)

    # Freshness skip: a non-expired result for this entity means the plugin
    # already has current evidence; record the run as skipped, don't execute.
    # Manual runs bypass this — the analyst explicitly asked for a fresh run.
    result_ttl = manifest.get("result_ttl_seconds")
    if not is_manual and event_object_id and isinstance(result_ttl, int) and result_ttl > 0:
        fresh = (
            await session.execute(
                select(PluginResult).where(
                    PluginResult.plugin_id == plugin_id,
                    PluginResult.entity_type == event_object_type,
                    PluginResult.entity_id == event_object_id,
                    PluginResult.expires_at.isnot(None),
                    PluginResult.expires_at > now,
                )
            )
        ).first()
        if fresh is not None:
            return await _skipped_run(
                session, body, event_type, organisation_id, plugin_id,
                plugin_version_id, runner_id, event_object_type, event_object_id,
                skip_reason="fresh_result",
            )

    # TLP/PAP skip: don't send entity details to a plugin whose declared handling
    # ceiling is below the entity's classification.
    max_tlp = manifest.get("max_tlp")
    max_pap = manifest.get("max_pap")
    if event_object_id and (max_tlp is not None or max_pap is not None):
        tlp, pap = await _entity_tlp_pap(session, event_object_type, event_object_id)
        if max_tlp is not None and tlp is not None and tlp > max_tlp:
            return await _skipped_run(
                session, body, event_type, organisation_id, plugin_id,
                plugin_version_id, runner_id, event_object_type, event_object_id,
                skip_reason="tlp_exceeded",
            )
        if max_pap is not None and pap is not None and pap > max_pap:
            return await _skipped_run(
                session, body, event_type, organisation_id, plugin_id,
                plugin_version_id, runner_id, event_object_type, event_object_id,
                skip_reason="pap_exceeded",
            )

    # Global concurrency cap: enforced here (API sees all runners), so N runners
    # cannot multiply a rate-limited plugin's live runs past its manifest cap.
    max_concurrent = manifest.get("max_concurrent_runs")
    if isinstance(max_concurrent, int) and max_concurrent > 0:
        active = (
            await session.execute(
                select(func.count(PluginRun.id)).where(
                    PluginRun.plugin_id == plugin_id,
                    PluginRun.status.in_(_ACTIVE_RUN_STATUSES),
                )
            )
        ).scalar_one()
        if active >= max_concurrent:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "message": "Plugin is at its concurrency limit; retry later",
                    "retry_after_seconds": 5,
                },
            )

    runtime_token = secrets.token_urlsafe(32)
    run = PluginRun(
        event_id=body["event_id"],
        event_type=event_type,
        organisation_id=organisation_id,
        plugin_id=plugin_id,
        plugin_version_id=plugin_version_id,
        runner_id=runner_id,
        event_object_type=event_object_type,
        event_object_id=event_object_id,
        permissions=list(manifest.get("permissions", [])),
        runtime_token_hash=_hash_runtime_token(runtime_token),
        runtime_token_expires_at=now + timedelta(
            seconds=settings.PLUGIN_RUNTIME_TOKEN_TTL_SECONDS
        ),
        status="queued",
    )
    session.add(run)
    await session.flush()
    return {"run_id": str(run.id), "status": run.status, "runtime_token": runtime_token}


async def _entity_tlp_pap(
    session: AsyncSession, obj_type: str | None, obj_id: str | None
) -> tuple[int | None, int | None]:
    """Return (tlp, pap) for the event's entity. Observables carry tlp only."""
    if not obj_type or not obj_id:
        return None, None
    try:
        if obj_type == "observable":
            obs = await obs_crud.get_observable(session, uuid.UUID(obj_id))
            return (obs.tlp if obs else None), None
        if obj_type == "case":
            case = await case_crud.get_case(session, int(obj_id))
            return (case.tlp, case.pap) if case else (None, None)
        if obj_type == "alert":
            alert = await alert_crud.get_alert(session, int(obj_id))
            return (alert.tlp, alert.pap) if alert else (None, None)
    except (ValueError, TypeError):
        return None, None
    return None, None


async def _skipped_run(
    session: AsyncSession,
    body: dict,
    event_type: str,
    organisation_id: str,
    plugin_id: str,
    plugin_version_id: str,
    runner_id: str,
    event_object_type: str | None,
    event_object_id: str | None,
    *,
    skip_reason: str,
) -> dict:
    """Record a terminal skipped run (freshness/TLP) without minting a token."""
    run = PluginRun(
        event_id=body["event_id"],
        event_type=event_type,
        organisation_id=organisation_id,
        plugin_id=plugin_id,
        plugin_version_id=plugin_version_id,
        runner_id=runner_id,
        event_object_type=event_object_type,
        event_object_id=event_object_id,
        status="skipped",
        skip_reason=skip_reason,
        ended_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return {"run_id": str(run.id), "status": "skipped", "skip_reason": skip_reason}


@router.post("/runs/{run_id}/accepted")
async def accept_run(
    run_id: uuid.UUID,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.runner_id != principal.runner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.status = "accepted"
    await session.flush()
    return {"status": run.status}


@router.post("/runs/{run_id}/started")
async def start_run(
    run_id: uuid.UUID,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.runner_id != principal.runner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.status = "running"
    run.started_at = datetime.now(UTC)
    await session.flush()
    return {"status": run.status}


@router.post("/runs/{run_id}/skipped")
async def skip_run(
    run_id: uuid.UUID,
    body: dict,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.runner_id != principal.runner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.status = "skipped"
    run.skip_reason = body.get("skip_reason", "")
    run.ended_at = datetime.now(UTC)
    # Token is intentionally left resolvable: a late runtime call now reaches the
    # status check in get_plugin_runtime_principal, which rejects it (409) and
    # audits the late result. The terminal status — not a null token — is what
    # makes the token accept no further work.
    await session.flush()
    return {"status": run.status}


@router.get("/runs/{run_id}/config")
async def get_run_config(
    run_id: uuid.UUID,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """Fetch decrypted config/secrets for a specific run.
    Only allowed while the run is accepted or running."""
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.runner_id != principal.runner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.status not in ("accepted", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Config only available for accepted or running runs",
        )

    cfg = await session.get(PluginConfig, (run.organisation_id, run.plugin_id))
    settings = cfg.settings if cfg else {}
    secrets: dict = {}
    if cfg and cfg.secrets_encrypted:
        from app.core.crypto import decrypt_secrets
        secrets = decrypt_secrets(cfg.secrets_encrypted)

    return {"settings": settings, "secrets": secrets}


@router.post("/runs/{run_id}/result")
async def submit_result(
    run_id: uuid.UUID,
    body: dict,
    principal: PluginRunner,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    run = await session.get(PluginRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.runner_id != principal.runner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    run.status = body["status"]
    run.result_summary = body.get("result_summary")
    run.operation_count = body.get("operation_count", 0)
    run.error = body.get("error")
    run.error_kind = body.get("error_kind")
    run.log_tail = body.get("log_tail")
    run.ended_at = datetime.now(UTC)
    if run.status in {"success", "failure", "timeout", "cancelled", "skipped"}:
        # Token is intentionally left resolvable (not nulled): a late runtime call
        # then reaches the status check in get_plugin_runtime_principal, which
        # rejects it (409) and audits the late result rather than hitting the
        # anonymous 401 branch. The terminal status makes the token accept no work.
        await circuit_breaker.record_run_outcome(
            session, run, threshold=settings.PLUGIN_CONFIG_FAILURE_THRESHOLD
        )
    await session.flush()
    return {"status": run.status}
