"""Plugin runtime API: internal endpoints for plugin reads and mutations.

These endpoints require a run-scoped plugin token. They enforce plugin
permissions, org scope, event context, and TLP/PAP policy.
"""
import hashlib
import uuid

from httpx import AsyncClient

from tests.test_api_plugin_runners import (
    RUNNER1,
    SAMPLE_MANIFEST,
    _enable_plugin_for_org,
    _register_runner,
)


_RUNTIME_PREFIX = "/api/internal/plugin-runtime"
_RUNNER_PREFIX = "/api/internal/plugin-runner"

RUNTIME_MANIFEST = {
    **SAMPLE_MANIFEST,
    "triggers": ["observable.created", "case.created", "alert.created"],
    "permissions": [
        "read:case",
        "write:case",
        "read:alert",
        "read:observable",
        "write:observable",
        "write:observable_enrichment",
        "write:plugin_result",
        "write:task",
    ],
}


def _runtime_h(token: str):
    return {"Authorization": f"Bearer {token}"}


def _runner_h(secret: str):
    return {"Authorization": f"Bearer {secret}"}


# --- Helpers ---


async def _create_case_with_observable(
    client: AsyncClient, org_a, analyst_a_token,
) -> tuple[int, uuid.UUID]:
    """Create a case and an observable, return (case_id, observable_id)."""
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "Test Case", "description": "Desc"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    case_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4", "tlp": 2, "pap": 2},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    obs_id = uuid.UUID(r.json()["id"])
    return case_id, obs_id


async def _create_case_with_file_observable(
    client: AsyncClient, org_a, analyst_a_token, content: bytes = b"sample-bytes",
) -> tuple[int, uuid.UUID]:
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    r = await client.post(
        "/api/v1/cases/",
        json={"title": "File Case", "description": "Desc"},
        headers=h,
    )
    assert r.status_code in (200, 201), r.text
    case_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/cases/{case_id}/observables/file",
        files={"file": ("sample.bin", content, "application/octet-stream")},
        data={"observable_type": "file"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return case_id, uuid.UUID(r.json()["id"])


async def _runtime_token_for(
    client: AsyncClient,
    runner_secret: str,
    admin_token: str,
    org_id: str,
    *,
    manifest: dict | None = None,
    event_type: str = "observable.created",
    event_object_type: str = "observable",
    event_object_id: str = "event-object",
) -> str:
    manifest = manifest or RUNTIME_MANIFEST
    _, credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[manifest],
    )
    h = _runner_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_id, manifest["id"])
    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json={
            "event_id": "audit:runtime",
            "event_type": event_type,
            "organisation_id": org_id,
            "plugin_id": manifest["id"],
            "plugin_version": manifest["version"],
            "runner_id": RUNNER1["id"],
            "event_object": {"type": event_object_type, "id": event_object_id},
            "trigger_metadata": {},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    await client.post(f"{_RUNNER_PREFIX}/runs/{data['run_id']}/accepted", headers=h)
    return data["runtime_token"]


# --- Read endpoints ---


async def test_read_observable(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    r = await client.get(
        f"{_RUNTIME_PREFIX}/observables/{obs_id}",
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"] == str(obs_id)
    assert data["data"] == "1.2.3.4"


async def test_read_case(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    r = await client.get(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == case_id


async def test_read_requires_valid_token(
    client: AsyncClient, org_a, analyst_a_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)

    assert (
        await client.get(f"{_RUNTIME_PREFIX}/cases/{case_id}")
    ).status_code == 401
    for token in ("nope", "test-runtime-secret"):
        assert (
            await client.get(
                f"{_RUNTIME_PREFIX}/cases/{case_id}",
                headers=_runtime_h(token),
            )
        ).status_code == 401


# --- Mutation endpoints ---


async def test_add_enrichment(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    r = await client.post(
        f"{_RUNTIME_PREFIX}/observables/{obs_id}/enrichments",
        json={
            "source": "acme-threatintel",
            "data": {"score": 75, "reputation": "bad"},
            "verdict": "suspicious",
        },
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text


async def test_add_generic_plugin_result_is_idempotent(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )
    body = {
        "entity_type": "observable",
        "entity_id": str(obs_id),
        "source": "acme-threatintel",
        "title": "Reputation",
        "summary": "Suspicious IP reputation",
        "verdict": "suspicious",
        "confidence": 0.82,
        "render_mode": "key_value",
        "normalized_data": {"score": 82},
        "raw_data": {"vendor": {"score": 82}},
        "attachments": [{"name": "evidence.json", "ref": "file:abc"}],
        "fingerprint": "observable-reputation-1",
    }

    first = await client.post(
        f"{_RUNTIME_PREFIX}/results",
        json=body,
        headers=_runtime_h(runtime_token),
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        f"{_RUNTIME_PREFIX}/results",
        json=body,
        headers=_runtime_h(runtime_token),
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["created"] is False


async def test_upload_runtime_file_and_attach_to_result(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )
    content = b"plugin evidence bytes"

    uploaded = await client.post(
        f"{_RUNTIME_PREFIX}/files",
        files={"file": ("evidence.bin", content, "application/octet-stream")},
        headers=_runtime_h(runtime_token),
    )
    assert uploaded.status_code == 200, uploaded.text
    file_ref = uploaded.json()
    assert file_ref["file_ref"].startswith("plugin-run-file:")
    assert file_ref["filename"] == "evidence.bin"
    assert file_ref["sha256"] == hashlib.sha256(content).hexdigest()
    assert file_ref["size"] == len(content)

    result = await client.post(
        f"{_RUNTIME_PREFIX}/results",
        json={
            "entity_type": "observable",
            "entity_id": str(obs_id),
            "summary": "Evidence with attachment",
            "attachments": [file_ref],
            "fingerprint": "evidence-with-file",
        },
        headers=_runtime_h(runtime_token),
    )
    assert result.status_code == 200, result.text
    assert result.json()["created"] is True


async def test_result_rejects_unknown_runtime_file_attachment(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    result = await client.post(
        f"{_RUNTIME_PREFIX}/results",
        json={
            "entity_type": "observable",
            "entity_id": str(obs_id),
            "summary": "Evidence with unknown attachment",
            "attachments": [
                {
                    "file_ref": f"plugin-run-file:{uuid.uuid4()}",
                    "filename": "missing.bin",
                    "content_type": "application/octet-stream",
                    "size": 1,
                    "sha256": "0" * 64,
                }
            ],
            "fingerprint": "unknown-file",
        },
        headers=_runtime_h(runtime_token),
    )
    assert result.status_code == 422


async def test_download_triggering_file_observable_with_runtime_token(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    content = b"runtime input file"
    _, obs_id = await _create_case_with_file_observable(
        client, org_a, analyst_a_token, content
    )
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    downloaded = await client.get(
        f"{_RUNTIME_PREFIX}/files/observable:{obs_id}",
        headers=_runtime_h(runtime_token),
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == content
    assert downloaded.headers["x-sha256"] == hashlib.sha256(content).hexdigest()
    assert "sample.bin" in downloaded.headers["content-disposition"]


async def test_update_run_progress_with_runtime_token(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    r = await client.post(
        f"{_RUNTIME_PREFIX}/progress",
        json={"message": "Querying vendor", "percent": 40},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["progress_message"] == "Querying vendor"
    assert r.json()["progress_percent"] == 40


async def test_runtime_token_is_invalid_after_terminal_status(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id)
    )

    # Resolve the run id through a progress write, then mark the run terminal via runner API.
    progress = await client.post(
        f"{_RUNTIME_PREFIX}/progress",
        json={"message": "Done", "percent": 100},
        headers=_runtime_h(runtime_token),
    )
    run_id = progress.json()["run_id"]

    _, runner_credential = await _register_runner(
        client,
        admin_token,
        RUNNER1,
        plugins=[RUNTIME_MANIFEST],
    )
    terminal = await client.post(
        f"{_RUNNER_PREFIX}/runs/{run_id}/result",
        json={"status": "success", "result_summary": {"ok": True}},
        headers=_runner_h(runner_credential),
    )
    assert terminal.status_code == 200, terminal.text

    late = await client.post(
        f"{_RUNTIME_PREFIX}/progress",
        json={"message": "late", "percent": 100},
        headers=_runtime_h(runtime_token),
    )
    assert late.status_code == 401


async def test_patch_case(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    r = await client.patch(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        json={"description": "Updated by plugin"},
        headers=_runtime_h(runtime_token),
    )
    # Canonical case edits are proposed, not applied directly.
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "proposed"
    assert r.json()["proposed_action_id"]


async def test_create_task(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    r = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tasks",
        json={"title": "Investigate", "description": "Check this IP"},
        headers=_runtime_h(runtime_token),
    )
    # Task creation is proposed, not applied directly.
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "proposed"


async def test_add_comment(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    r = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/comments",
        json={"message": "Auto-comment from plugin"},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 200, r.text


async def test_runtime_token_cannot_read_another_org_case(
    client: AsyncClient,
    org_a,
    org_b,
    analyst_a_token,
    analyst_b_token,
    runner_secret,
    admin_token,
):
    await _create_case_with_observable(client, org_a, analyst_a_token)
    other_case_id, _ = await _create_case_with_observable(client, org_b, analyst_b_token)
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id="1",
    )

    r = await client.get(
        f"{_RUNTIME_PREFIX}/cases/{other_case_id}",
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 404


async def test_runtime_permission_required_for_case_patch(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    manifest = {
        **RUNTIME_MANIFEST,
        "permissions": ["read:case"],
    }
    runtime_token = await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        manifest=manifest,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )

    r = await client.patch(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        json={"description": "Blocked"},
        headers=_runtime_h(runtime_token),
    )
    assert r.status_code == 403
