"""Org-wide analyzer-job queue: GET /enrichment-jobs lists the active org's
enrichment jobs (status-filtered, paginated, tenant-isolated), GET /{id} surfaces
the report + verdict badges, and cancel/retry-failed/clear-finished manage the queue."""
import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import enrichment as enrichment_crud
from app.models.case_ import CaseCreate
from app.models.connector import Verdict
from app.models.enrichment import JobStatus, ReportTag

GEOIP = {"name": "geoip2", "display_name": "GeoIP", "version": "1.0.0", "data_types": ["ip"]}


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _analyzer_h(secret):
    return {"Authorization": f"Bearer {secret}"}


async def _register(client, secret, *connectors):
    return await client.post(
        "/api/internal/analyzer/register",
        json={"connectors": list(connectors)},
        headers=_analyzer_h(secret),
    )


async def _enable(client, token, org_id, name):
    return await client.post(f"/api/v1/connectors/{name}/enable", headers=_h(token, org_id))


async def _ip_observable(client, session, org, builtin_roles, user, token, data="1.2.3.4"):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": data},
        headers=_h(token, org.id),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _enqueue(client, session, org, builtin_roles, user, token, data="1.2.3.4"):
    obs_id = await _ip_observable(client, session, org, builtin_roles, user, token, data)
    r = await client.post(
        f"/api/v1/observables/{obs_id}/enrich", json={}, headers=_h(token, org.id)
    )
    assert r.status_code == 200, r.text
    return r.json()[0]["id"]


async def _set(session, job_id, **fields):
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    for key, value in fields.items():
        setattr(job, key, value)
    session.add(job)
    await session.commit()
    return job


async def test_list_returns_org_jobs_with_display_name(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    r = await client.get("/api/v1/enrichment-jobs", headers=_h(analyst_a_token, org_a.id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["connector_name"] == "geoip2"
    assert row["connector_display_name"] == "GeoIP"
    assert row["data_type"] == "ip"
    assert row["data"] == "1.2.3.4"
    assert row["status"] == JobStatus.queued.value


async def test_list_filters_by_status(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    h = _h(analyst_a_token, org_a.id)
    queued = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "1.1.1.1")
    done = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "2.2.2.2")
    running = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "3.3.3.3")
    await _set(session, done, status=JobStatus.success.value, ended_at=datetime.now(UTC))
    await _set(session, running, status=JobStatus.leased.value)

    only_queued = (await client.get("/api/v1/enrichment-jobs?status=queued", headers=h)).json()
    assert [i["id"] for i in only_queued["items"]] == [queued]

    only_running = (await client.get("/api/v1/enrichment-jobs?status=running", headers=h)).json()
    assert [i["id"] for i in only_running["items"]] == [running]

    only_success = (await client.get("/api/v1/enrichment-jobs?status=success", headers=h)).json()
    assert [i["id"] for i in only_success["items"]] == [done]

    all_jobs = (await client.get("/api/v1/enrichment-jobs", headers=h)).json()
    assert all_jobs["total"] == 3


async def test_list_is_tenant_isolated(
    client: AsyncClient, session, analyzer_secret, org_a, org_b, builtin_roles,
    observable_types, analyst_a, analyst_a_token, analyst_b_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    seen_by_b = (await client.get("/api/v1/enrichment-jobs", headers=_h(analyst_b_token, org_b.id))).json()
    assert seen_by_b["total"] == 0
    assert seen_by_b["items"] == []


async def test_detail_returns_report_and_tags(
    client: AsyncClient, session, analyzer_secret, org_a, org_b, builtin_roles,
    observable_types, analyst_a, analyst_a_token, analyst_b_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    job_id = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    job = await _set(
        session, job_id,
        status=JobStatus.success.value,
        verdict=Verdict.malicious.value,
        report={"abuse_score": 97},
        ended_at=datetime.now(UTC),
    )
    session.add(
        ReportTag(
            observable_id=job.observable_id, job_id=job.id, connector_name="geoip2",
            namespace="geo", predicate="country", value="RU", level=Verdict.malicious.value,
        )
    )
    await session.commit()

    detail = (await client.get(f"/api/v1/enrichment-jobs/{job_id}", headers=_h(analyst_a_token, org_a.id))).json()
    assert detail["report"] == {"abuse_score": 97}
    assert detail["verdict"] == Verdict.malicious.value
    assert detail["tags"][0]["value"] == "RU"

    # Cross-org access is a 404, not a 403 (don't leak existence).
    cross = await client.get(f"/api/v1/enrichment-jobs/{job_id}", headers=_h(analyst_b_token, org_b.id))
    assert cross.status_code == 404


async def test_cancel_removes_queued_but_not_terminal(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    h = _h(analyst_a_token, org_a.id)
    job_id = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    cancelled = await client.post(f"/api/v1/enrichment-jobs/{job_id}/cancel", headers=h)
    assert cancelled.status_code == 204
    assert (await client.get("/api/v1/enrichment-jobs", headers=h)).json()["total"] == 0

    done = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "9.9.9.9")
    await _set(session, done, status=JobStatus.success.value, ended_at=datetime.now(UTC))
    conflict = await client.post(f"/api/v1/enrichment-jobs/{done}/cancel", headers=h)
    assert conflict.status_code == 409


async def test_retry_failed_requeues(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    h = _h(analyst_a_token, org_a.id)
    job_id = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    await _set(session, job_id, status=JobStatus.failure.value, error="boom", attempts=2,
               ended_at=datetime.now(UTC))

    r = await client.post("/api/v1/enrichment-jobs/retry-failed", headers=h)
    assert r.status_code == 200
    assert r.json() == {"requeued": 1}

    requeued = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    await session.refresh(requeued)
    assert requeued.status == JobStatus.queued.value
    assert requeued.error is None
    assert requeued.attempts == 0


async def test_clear_finished_removes_terminal_only(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    h = _h(analyst_a_token, org_a.id)
    queued = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "1.1.1.1")
    done = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "2.2.2.2")
    failed = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token, "3.3.3.3")
    await _set(session, done, status=JobStatus.success.value, ended_at=datetime.now(UTC))
    await _set(session, failed, status=JobStatus.failure.value, ended_at=datetime.now(UTC))

    r = await client.post("/api/v1/enrichment-jobs/clear-finished", headers=h)
    assert r.status_code == 200
    assert r.json() == {"cleared": 2}

    remaining = (await client.get("/api/v1/enrichment-jobs", headers=h)).json()
    assert [i["id"] for i in remaining["items"]] == [queued]


async def test_mutations_require_run_enrichment_perm(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token, readonly_a, readonly_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    job_id = await _enqueue(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    ro = _h(readonly_a_token, org_a.id)

    # Read-only members can watch the queue but not act on it.
    assert (await client.get("/api/v1/enrichment-jobs", headers=ro)).status_code == 200
    assert (await client.post(f"/api/v1/enrichment-jobs/{job_id}/cancel", headers=ro)).status_code == 403
    assert (await client.post("/api/v1/enrichment-jobs/retry-failed", headers=ro)).status_code == 403
    assert (await client.post("/api/v1/enrichment-jobs/clear-finished", headers=ro)).status_code == 403
