"""Responder claim/result contract: real queue lease + operation application."""

import uuid

from httpx import AsyncClient
from sqlmodel import select

from app.crud import task as task_crud
from app.models.case_ import Case
from app.models.comment import Comment, CommentEntityType
from app.models.enrichment import EnrichmentJob, JobStatus
from app.models.observable import Observable
from app.models.task import Task, TaskCreate, TaskStatus


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _analyzer_h(secret):
    return {"Authorization": f"Bearer {secret}"}


RESPONDER = {
    "name": "test_responder",
    "display_name": "Test Responder",
    "connector_type": "responder",
    "version": "1.0.0",
    "data_types": ["domain"],
    "description": "test responder",
    "max_runtime_seconds": 5,
}

OTHER_RESPONDER = {
    **RESPONDER,
    "name": "other_responder",
    "display_name": "Other Responder",
}


async def _seed_responder_job(
    client: AsyncClient,
    session,
    analyzer_secret,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    r = await client.post(
        "/api/internal/analyzer/register",
        json={"connectors": [RESPONDER]},
        headers=_analyzer_h(analyzer_secret),
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/v1/connectors/test_responder/enable",
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text

    case = await client.post(
        "/api/v1/cases/",
        json={"title": "responder case"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert case.status_code == 201, case.text
    case_id = case.json()["id"]
    obs = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "domain", "data": "example.test"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert obs.status_code == 201, obs.text
    obs_id = obs.json()["id"]

    jobs = await client.post(
        f"/api/v1/observables/{obs_id}/enrich",
        json={"connector": "test_responder", "force_refresh": True},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert jobs.status_code == 200, jobs.text
    return case_id, obs_id, jobs.json()[0]["id"]


async def test_responder_claim_and_result_applies_operation(
    client: AsyncClient,
    session,
    analyzer_secret,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case_id, _, job_id = await _seed_responder_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )

    claim = await client.post(
        "/api/internal/responder/work",
        headers=_analyzer_h(analyzer_secret),
        params={"limit": 10},
    )
    assert claim.status_code == 200, claim.text
    items = claim.json()
    assert [i["job_id"] for i in items] == [job_id]
    lease_token = items[0]["lease_token"]

    result = await client.post(
        f"/api/internal/responder/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={
            "lease_token": lease_token,
            "status": "success",
            "message": "ok",
            "operations": [
                {"kind": "add_comment", "params": {"message": "responder comment"}}
            ],
        },
    )
    assert result.status_code == 200, result.text

    job = await session.get(EnrichmentJob, uuid.UUID(job_id))
    assert job.status == JobStatus.success.value
    assert job.lease_token is None

    comments = (
        await session.execute(
            select(Comment).where(
                Comment.entity_type == CommentEntityType.case,
                Comment.entity_id == str(case_id),
            )
        )
    ).scalars().all()
    assert [c.message for c in comments] == ["responder comment"]


async def test_responder_invalid_lease_rejected(
    client: AsyncClient,
    session,
    analyzer_secret,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    _, _, job_id = await _seed_responder_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    await client.post(
        "/api/internal/responder/work",
        headers=_analyzer_h(analyzer_secret),
        params={"limit": 10},
    )

    result = await client.post(
        f"/api/internal/responder/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={"lease_token": str(uuid.uuid4()), "status": "success"},
    )
    assert result.status_code == 409


async def test_responder_claim_filters_by_loaded_connectors_and_includes_config(
    client: AsyncClient,
    analyzer_secret,
    admin_token,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case_id, _, job_id = await _seed_responder_job(
        client, None, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    del case_id

    r = await client.post(
        "/api/internal/analyzer/register",
        json={"connectors": [OTHER_RESPONDER]},
        headers=_analyzer_h(analyzer_secret),
    )
    assert r.status_code == 200, r.text
    cfg = await client.put(
        "/api/v1/connectors/test_responder/config",
        json={"settings": {"base_url": "https://example.test"}, "secrets": {"api_key": "secret"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cfg.status_code == 200, cfg.text

    miss = await client.post(
        "/api/internal/responder/work",
        headers=_analyzer_h(analyzer_secret),
        params={"connectors": "other_responder", "limit": 10},
    )
    assert miss.status_code == 200, miss.text
    assert miss.json() == []

    claim = await client.post(
        "/api/internal/responder/work",
        headers=_analyzer_h(analyzer_secret),
        params={"connectors": "test_responder", "limit": 10},
    )
    assert claim.status_code == 200, claim.text
    items = claim.json()
    assert [i["job_id"] for i in items] == [job_id]
    assert items[0]["connector_type"] == "responder"
    assert items[0]["config"] == {
        "base_url": "https://example.test",
        "api_key": "secret",
    }


async def test_responder_result_applies_supported_mutating_operations(
    client: AsyncClient,
    session,
    analyzer_secret,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case_id, obs_id, job_id = await _seed_responder_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    existing_task = await task_crud.create_task(
        session,
        TaskCreate(title="close me"),
        case_id=case_id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )

    claim = await client.post(
        "/api/internal/responder/work",
        headers=_analyzer_h(analyzer_secret),
        params={"connectors": "test_responder", "limit": 10},
    )
    lease_token = claim.json()[0]["lease_token"]

    result = await client.post(
        f"/api/internal/responder/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={
            "lease_token": lease_token,
            "status": "success",
            "operations": [
                {"kind": "create_task", "params": {"title": "from responder"}},
                {
                    "kind": "update_observable",
                    "params": {
                        "observable_id": obs_id,
                        "message": "updated by responder",
                        "ioc": True,
                    },
                },
                {
                    "kind": "assign_case",
                    "params": {"assignee_id": str(analyst_a.id)},
                },
                {
                    "kind": "close_task",
                    "params": {"task_id": existing_task.public_id},
                },
            ],
        },
    )
    assert result.status_code == 200, result.text

    await session.refresh(existing_task)
    assert existing_task.status == TaskStatus.completed

    tasks = (
        await session.execute(
            select(Task).where(Task.case_id == case_id).order_by(Task.id)
        )
    ).scalars().all()
    assert [task.title for task in tasks] == ["close me", "from responder"]

    obs = await session.get(Observable, uuid.UUID(obs_id))
    assert obs is not None
    assert obs.message == "updated by responder"
    assert obs.ioc is True

    case = await session.get(Case, case_id)
    assert case is not None
    assert case.assignee_id == analyst_a.id
