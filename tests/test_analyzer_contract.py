"""Analyzer pull/lease contract: claim leases work (with decrypted config), a second
poll never double-hands a leased job, results import artifacts + write/replace verdict
badges, the failure path records the error, expired leases get reclaimed, and the
attempts cap fails a job out of the queue."""
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import enrichment as enrichment_crud
from app.crud import observable as obs_crud
from app.models.case_ import CaseCreate
from app.models.connector import Verdict
from app.models.enrichment import JobStatus

GEOIP = {"name": "geoip2", "display_name": "GeoIP", "version": "1.0.0", "data_types": ["ip"]}


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _analyzer_h(secret):
    return {"Authorization": f"Bearer {secret}"}


async def _register(client, secret, *connectors):
    return await client.post(
        "/api/v1/analyzer/register",
        json={"connectors": list(connectors)},
        headers=_analyzer_h(secret),
    )


async def _seed_job(
    client, session, secret, org, builtin_roles, user, token, *, admin_token=None
):
    """Register + enable geoip2, create an ip case observable, enrich it, and return
    (case_id, observable_id, job_id) for a single queued job."""
    await _register(client, secret, GEOIP)
    if admin_token is not None:
        await client.put(
            "/api/v1/connectors/geoip2/config",
            json={"settings": {"region": "eu"}, "secrets": {"api_key": "s3cr3t"}},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    await client.post(f"/api/v1/connectors/geoip2/enable", headers=_h(token, org.id))
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )
    obs = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": "1.2.3.4"},
        headers=_h(token, org.id),
    )
    obs_id = obs.json()["id"]
    job = (
        await client.post(
            f"/api/v1/observables/{obs_id}/enrich", json={}, headers=_h(token, org.id)
        )
    ).json()[0]
    return case.id, obs_id, job["id"]


async def _claim(client, secret, **params):
    r = await client.post("/api/v1/analyzer/work", headers=_analyzer_h(secret), params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_claim_returns_lease_and_decrypted_config(
    client: AsyncClient, session, analyzer_secret, admin_token, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a,
        analyst_a_token, admin_token=admin_token,
    )
    items = await _claim(client, analyzer_secret, connectors="geoip2", limit=10)
    assert len(items) == 1
    item = items[0]
    assert item["job_id"] == job_id
    assert item["lease_token"]
    # Global settings + decrypted secrets are shipped to the trusted analyzer.
    assert item["config"] == {"region": "eu", "api_key": "s3cr3t"}

    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    assert job.status == JobStatus.leased.value
    assert job.attempts == 1


async def test_work_requires_analyzer_secret(client: AsyncClient, analyzer_secret):
    assert (await client.post("/api/v1/analyzer/work")).status_code == 401
    bad = await client.post("/api/v1/analyzer/work", headers=_analyzer_h("nope"))
    assert bad.status_code == 401


async def test_concurrent_claim_does_not_double_hand(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    first = await _claim(client, analyzer_secret, limit=10)
    assert [i["job_id"] for i in first] == [job_id]
    # A second poll while the lease is still valid gets nothing.
    assert await _claim(client, analyzer_secret, limit=10) == []


async def test_result_success_imports_artifacts_and_writes_tags(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    case_id, obs_id, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    item = (await _claim(client, analyzer_secret, limit=10))[0]
    r = await client.post(
        f"/api/v1/analyzer/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={
            "lease_token": item["lease_token"],
            "status": "success",
            "verdict": "malicious",
            "taxonomies": [
                {"namespace": "geo", "predicate": "country", "value": "RU", "level": "malicious"}
            ],
            "artifacts": [{"observable_type": "domain", "data": "evil.test"}],
            "full": {"raw": {"asn": 1234}},
        },
    )
    assert r.status_code == 200, r.text

    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    assert job.status == JobStatus.success.value
    assert job.verdict == Verdict.malicious.value
    assert job.report == {"raw": {"asn": 1234}}

    tags = await enrichment_crud.list_report_tags(session, uuid.UUID(obs_id))
    assert [(t.predicate, t.value) for t in tags] == [("country", "RU")]

    # The returned artifact landed as a deduped case observable.
    imported = await obs_crud.find_case_observable(session, case_id, "domain", "evil.test")
    assert imported is not None


async def test_new_result_replaces_prior_tags(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, obs_id, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    item = (await _claim(client, analyzer_secret, limit=10))[0]
    await client.post(
        f"/api/v1/analyzer/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={
            "lease_token": item["lease_token"],
            "status": "success",
            "verdict": "safe",
            "taxonomies": [{"namespace": "geo", "predicate": "country", "value": "US"}],
        },
    )

    # Re-run the same connector; its new badge set should replace the old one.
    job2_id = (
        await client.post(
            f"/api/v1/observables/{obs_id}/enrich",
            json={"force_refresh": True},
            headers=_h(analyst_a_token, org_a.id),
        )
    ).json()[0]["id"]
    item2 = (await _claim(client, analyzer_secret, limit=10))[0]
    assert item2["job_id"] == job2_id
    await client.post(
        f"/api/v1/analyzer/jobs/{job2_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={
            "lease_token": item2["lease_token"],
            "status": "success",
            "verdict": "malicious",
            "taxonomies": [{"namespace": "geo", "predicate": "country", "value": "RU"}],
        },
    )

    tags = await enrichment_crud.list_report_tags(session, uuid.UUID(obs_id))
    assert [t.value for t in tags] == ["RU"]


async def test_failure_result_records_error(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    item = (await _claim(client, analyzer_secret, limit=10))[0]
    r = await client.post(
        f"/api/v1/analyzer/jobs/{job_id}/result",
        headers=_analyzer_h(analyzer_secret),
        json={"lease_token": item["lease_token"], "status": "failure", "error": "upstream 503"},
    )
    assert r.status_code == 200

    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    assert job.status == JobStatus.failure.value
    assert job.error == "upstream 503"


async def test_invalid_lease_and_status_rejected(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    await _claim(client, analyzer_secret, limit=10)
    h = _analyzer_h(analyzer_secret)

    wrong = await client.post(
        f"/api/v1/analyzer/jobs/{job_id}/result",
        headers=h,
        json={"lease_token": str(uuid.uuid4()), "status": "success"},
    )
    assert wrong.status_code == 409

    bad_status = await client.post(
        f"/api/v1/analyzer/jobs/{job_id}/result",
        headers=h,
        json={"lease_token": str(uuid.uuid4()), "status": "bogus"},
    )
    assert bad_status.status_code == 422

    missing = await client.post(
        f"/api/v1/analyzer/jobs/{uuid.uuid4()}/result",
        headers=h,
        json={"lease_token": str(uuid.uuid4()), "status": "success"},
    )
    assert missing.status_code == 404


async def test_expired_lease_is_reclaimed(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    assert len(await _claim(client, analyzer_secret, limit=10)) == 1

    # Age the lease past expiry.
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
    session.add(job)
    await session.commit()

    reclaimed = await _claim(client, analyzer_secret, limit=10)
    assert [i["job_id"] for i in reclaimed] == [job_id]
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    assert job.attempts == 2


async def test_attempts_cap_fails_job_out_of_queue(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    from app.core.configs import settings

    _, _, job_id = await _seed_job(
        client, session, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
    )
    # Simulate a job that has already burned all its attempts with an expired lease.
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    job.status = JobStatus.leased.value
    job.attempts = settings.ANALYZER_MAX_ATTEMPTS
    job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
    session.add(job)
    await session.commit()

    # The poll should retire it rather than hand it out again.
    assert await _claim(client, analyzer_secret, limit=10) == []
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    assert job.status == JobStatus.failure.value
    assert job.error == "max lease attempts exceeded"
