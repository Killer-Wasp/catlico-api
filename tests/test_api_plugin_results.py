"""Public read surface for plugin results (Plugin Results / Enrichment panel).

Results ride entity visibility: a viewer sees plugin results for an entity iff they
can already read that entity (same guard, same 404/403). Newest first; expired
results are flagged ``stale`` but never hidden; a shared entity's results (produced by
the owner org) are visible to the shared org.
"""
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.models.case_ import CaseCreate
from app.models.case_share import CaseShare
from app.models.observable import ObservableShare
from app.models.plugin_runner import PluginDefinition, PluginResult


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )
    await session.commit()
    return case


async def _make_observable(client, case_id, token, org_id, data="1.2.3.4"):
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": data},
        headers=_headers(token, org_id),
    )
    assert r.status_code in (200, 201), r.text
    return uuid.UUID(r.json()["id"])


async def _ensure_plugin(session, plugin_id):
    if await session.get(PluginDefinition, plugin_id) is None:
        session.add(PluginDefinition(id=plugin_id, created_by="system"))
        await session.commit()


async def _add_result(session, *, org_id, entity_type, entity_id, **overrides):
    now = datetime.now(UTC)
    plugin_id = overrides.get("plugin_id", "geoip")
    await _ensure_plugin(session, plugin_id)
    fields = dict(
        organisation_id=org_id,
        plugin_id=plugin_id,
        plugin_version_id="geoip@1.0.0",
        entity_type=entity_type,
        entity_id=entity_id,
        source="plugin",
        render_mode="json",
        fingerprint=f"fp-{uuid.uuid4()}",
        created_at=now,
    )
    fields.update(overrides)
    result = PluginResult(**fields)
    session.add(result)
    await session.commit()
    return result


# --- Observable results: ordering, scoping, empty ---


async def test_observable_results_newest_first_and_scoped(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    other = await _make_observable(client, case.id, analyst_a_token, org_a.id, data="5.5.5.5")

    now = datetime.now(UTC)
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="older", created_at=now - timedelta(minutes=5),
    )
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="newer", created_at=now,
    )
    # A result on a different observable must not leak into this entity's list.
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(other),
        title="other-entity",
    )

    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["title"] for i in items] == ["newer", "older"]
    # raw_data is present in the list response (audit/debug field).
    assert "raw_data" in items[0]
    # latest per (plugin_id, source): newest is latest, older is not.
    assert items[0]["latest"] is True
    assert items[1]["latest"] is False


async def test_observable_no_results_returns_empty_list(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200
    assert r.json() == []


# --- Authorisation: no cross-org leak, same rejection as entity read ---


async def test_observable_results_not_visible_to_unrelated_org(
    client: AsyncClient, session, org_a, org_b, builtin_roles, observable_types,
    analyst_a, analyst_a_token, analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="secret",
    )
    # org_b has no share on the case: same 404 the observable-read route gives.
    read = await client.get(
        f"/api/v1/observables/{obs}", headers=_headers(analyst_b_token, org_b.id)
    )
    assert read.status_code == 404
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(analyst_b_token, org_b.id),
    )
    assert r.status_code == 404


async def test_shared_observable_results_visible_to_shared_org(
    client: AsyncClient, session, org_a, org_b, builtin_roles, observable_types,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    # Owner org produces a result on the shared observable.
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="owner-produced",
    )

    # Share the case (non-owner) and the observable to org_b.
    session.add(
        CaseShare(
            case_id=case.id, organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id, is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()

    hb = _headers(analyst_b_token, org_b.id)
    # Case shared but observable not yet shared -> 404, matching observable-read.
    assert (await client.get(f"/api/v1/observables/{obs}", headers=hb)).status_code == 404
    assert (
        await client.get(f"/api/v1/observables/{obs}/plugin-results", headers=hb)
    ).status_code == 404

    # Now share the observable itself.
    session.add(ObservableShare(observable_id=obs, organisation_id=org_b.id, created_by=str(analyst_a.id)))
    await session.commit()

    r = await client.get(f"/api/v1/observables/{obs}/plugin-results", headers=hb)
    assert r.status_code == 200, r.text
    items = r.json()
    assert [i["title"] for i in items] == ["owner-produced"]
    # Producing org stays visible in provenance.
    assert items[0]["organisation_id"] == org_a.id


# --- Staleness ---


async def test_stale_flag_and_expired_latest_still_returned(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    now = datetime.now(UTC)

    # Latest result is expired (stale) — must still be returned.
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="expired-latest", created_at=now,
        expires_at=now - timedelta(hours=1),
    )
    # An older, still-fresh result.
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="fresh-older", created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=1),
    )

    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    items = {i["title"]: i for i in r.json()}
    assert set(items) == {"expired-latest", "fresh-older"}
    assert items["expired-latest"]["stale"] is True
    assert items["expired-latest"]["latest"] is True
    assert items["fresh-older"]["stale"] is False


async def test_result_without_expiry_is_not_stale(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="no-expiry",
    )
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.json()[0]["stale"] is False


# --- Case and alert entity surfaces ---


async def test_case_results_scoped_and_guarded(
    client: AsyncClient, session, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    await _add_result(
        session, org_id=org_a.id, entity_type="case", entity_id=str(case.id),
        title="case-insight",
    )
    r = await client.get(
        f"/api/v1/cases/{case.id}/plugin-results",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()] == ["case-insight"]

    # Unshared org: same 404 the case-read route gives.
    assert (
        await client.get(
            f"/api/v1/cases/{case.id}/plugin-results",
            headers=_headers(analyst_b_token, org_b.id),
        )
    ).status_code == 404


async def test_task_results_do_not_collide_across_cases(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token,
):
    """Task ids are per-case sequences, so results are keyed by the composite
    ``"{case_id}:{task_id}"``. A result on case 1's task must not appear on
    another case's task that happens to share the same task number (previously
    a cross-case — and cross-org — leak)."""
    ha = _headers(analyst_a_token, org_a.id)
    case1 = await _make_case(session, org_a, builtin_roles, analyst_a)
    case2 = await _make_case(session, org_a, builtin_roles, analyst_a)

    t1 = (
        await client.post(
            f"/api/v1/cases/{case1.id}/tasks", json={"title": "triage"}, headers=ha
        )
    ).json()["id"]
    t2 = (
        await client.post(
            f"/api/v1/cases/{case2.id}/tasks", json={"title": "triage"}, headers=ha
        )
    ).json()["id"]
    # The collision scenario is only real if the per-case sequences align.
    assert t1 == t2

    await _add_result(
        session, org_id=org_a.id, entity_type="task",
        entity_id=f"{case1.id}:{t1}", title="task-insight",
    )

    r = await client.get(
        f"/api/v1/cases/{case1.id}/tasks/{t1}/plugin-results", headers=ha
    )
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()] == ["task-insight"]

    # Same task number on the other case: no leak.
    r = await client.get(
        f"/api/v1/cases/{case2.id}/tasks/{t2}/plugin-results", headers=ha
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_alert_results_scoped_and_guarded(
    client: AsyncClient, session, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/alerts/",
        json={"type": "phishing", "source": "gw", "source_ref": "e1",
              "title": "a", "description": "d", "severity": 2},
        headers=ha,
    )
    assert created.status_code == 201, created.text
    alert_id = created.json()["id"]
    await _add_result(
        session, org_id=org_a.id, entity_type="alert", entity_id=str(alert_id),
        title="alert-insight",
    )

    r = await client.get(f"/api/v1/alerts/{alert_id}/plugin-results", headers=ha)
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()] == ["alert-insight"]

    # Alerts have no sharing: another org gets 404, matching alert-read.
    assert (
        await client.get(
            f"/api/v1/alerts/{alert_id}/plugin-results",
            headers=_headers(analyst_b_token, org_b.id),
        )
    ).status_code == 404


async def test_readonly_user_can_read_results(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token, readonly_a_token,
):
    """read-only role has read:observable, so it can read plugin results too."""
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    await _add_result(
        session, org_id=org_a.id, entity_type="observable", entity_id=str(obs),
        title="visible",
    )
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results",
        headers=_headers(readonly_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert [i["title"] for i in r.json()] == ["visible"]
