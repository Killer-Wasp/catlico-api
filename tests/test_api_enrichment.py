"""User/admin-facing enrichment: POST /observables/{id}/enrich routes by data_type
to enabled connectors, dedups within the cache TTL (force_refresh bypasses), enforces
perms, and GET /observables/{id}/enrichments surfaces jobs + verdict badges."""
import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import enrichment as enrichment_crud
from app.models.case_ import CaseCreate
from app.models.connector import Verdict
from app.models.enrichment import JobStatus, ReportTag

GEOIP = {"name": "geoip2", "display_name": "GeoIP", "version": "1.0.0", "data_types": ["ip"]}
VT = {"name": "virustotal", "version": "2.0.0", "data_types": ["domain", "hash"]}


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


async def _make_case(session, org, builtin_roles, user):
    return await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


async def _ip_observable(client, session, org, builtin_roles, user, token, data="1.2.3.4"):
    case = await _make_case(session, org, builtin_roles, user)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "ip", "data": data},
        headers=_h(token, org.id),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_enrich_routes_by_data_type_and_enqueues(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP, VT)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    await _enable(client, analyst_a_token, org_a.id, "virustotal")
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    r = await client.post(
        f"/api/v1/observables/{obs_id}/enrich", json={}, headers=_h(analyst_a_token, org_a.id)
    )
    assert r.status_code == 200, r.text
    jobs = r.json()
    # virustotal doesn't accept "ip", so only geoip2 is dispatched.
    assert [j["connector_name"] for j in jobs] == ["geoip2"]
    assert jobs[0]["status"] == JobStatus.queued.value


async def test_enrich_no_matching_connector_409(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    # virustotal is enabled but only handles domain/hash, not the ip observable.
    await _register(client, analyzer_secret, VT)
    await _enable(client, analyst_a_token, org_a.id, "virustotal")
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    r = await client.post(
        f"/api/v1/observables/{obs_id}/enrich", json={}, headers=_h(analyst_a_token, org_a.id)
    )
    assert r.status_code == 409


async def test_enrich_named_connector_must_be_enabled(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)  # registered but NOT enabled for org
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    r = await client.post(
        f"/api/v1/observables/{obs_id}/enrich",
        json={"connector": "geoip2"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 409

    r2 = await client.post(
        f"/api/v1/observables/{obs_id}/enrich",
        json={"connector": "ghost"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r2.status_code == 404


async def _mark_success(session, job_id: str):
    job = await enrichment_crud.get_job(session, uuid.UUID(job_id))
    job.status = JobStatus.success.value
    job.verdict = Verdict.malicious.value
    job.ended_at = datetime.now(UTC)
    session.add(job)
    await session.commit()
    return job


async def test_enrich_dedups_within_ttl(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    h = _h(analyst_a_token, org_a.id)

    first = (await client.post(f"/api/v1/observables/{obs_id}/enrich", json={}, headers=h)).json()
    job_id = first[0]["id"]
    await _mark_success(session, job_id)

    # A second enrich within the TTL reuses the fresh successful job.
    again = (await client.post(f"/api/v1/observables/{obs_id}/enrich", json={}, headers=h)).json()
    assert again[0]["id"] == job_id
    assert again[0]["status"] == JobStatus.success.value


async def test_force_refresh_bypasses_cache(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    h = _h(analyst_a_token, org_a.id)

    first = (await client.post(f"/api/v1/observables/{obs_id}/enrich", json={}, headers=h)).json()
    job_id = first[0]["id"]
    await _mark_success(session, job_id)

    forced = (
        await client.post(
            f"/api/v1/observables/{obs_id}/enrich", json={"force_refresh": True}, headers=h
        )
    ).json()
    assert forced[0]["id"] != job_id
    assert forced[0]["status"] == JobStatus.queued.value


async def test_get_enrichments_returns_jobs_and_tags(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)
    h = _h(analyst_a_token, org_a.id)

    job_id = (await client.post(f"/api/v1/observables/{obs_id}/enrich", json={}, headers=h)).json()[0]["id"]
    job = await _mark_success(session, job_id)
    session.add(
        ReportTag(
            observable_id=job.observable_id,
            job_id=job.id,
            connector_name="geoip2",
            namespace="geo",
            predicate="country",
            value="RU",
            level=Verdict.malicious.value,
        )
    )
    await session.commit()

    overview = (await client.get(f"/api/v1/observables/{obs_id}/enrichments", headers=h)).json()
    assert overview["jobs"][0]["verdict"] == Verdict.malicious.value
    assert overview["tags"][0]["value"] == "RU"
    assert overview["tags"][0]["level"] == Verdict.malicious.value


async def test_enrich_requires_run_enrichment_perm(
    client: AsyncClient, session, analyzer_secret, org_a, builtin_roles,
    observable_types, analyst_a, analyst_a_token, readonly_a, readonly_a_token,
):
    await _register(client, analyzer_secret, GEOIP)
    await _enable(client, analyst_a_token, org_a.id, "geoip2")
    # analyst_a (org-admin) owns the case; readonly_a is in the same org and can read it.
    obs_id = await _ip_observable(client, session, org_a, builtin_roles, analyst_a, analyst_a_token)

    ro = _h(readonly_a_token, org_a.id)
    assert (await client.get(f"/api/v1/observables/{obs_id}/enrichments", headers=ro)).status_code == 200
    assert (
        await client.post(f"/api/v1/observables/{obs_id}/enrich", json={}, headers=ro)
    ).status_code == 403
