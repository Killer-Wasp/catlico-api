"""Tests for the SOC Overview aggregate (`GET /overview`)."""

from httpx import AsyncClient


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _alert_payload(**overrides):
    base = {
        "type": "phishing",
        "source": "mail-gw",
        "source_ref": "evt-001",
        "title": "Suspicious email",
        "description": "user reported",
        "severity": 2,
    }
    base.update(overrides)
    return base


async def test_overview_empty_org_has_stable_shape(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.get("/api/v1/overview", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    # All panels present, even with no data.
    for key in (
        "generated_at",
        "stats",
        "alerts_by_severity",
        "open_alerts_total",
        "triage_queue",
        "case_pipeline",
        "analyst_workload",
        "ingestion_24h",
        "latest_observables",
        "case_trend",
        "resolution_breakdown",
        "alerts_by_source",
    ):
        assert key in body, key

    assert body["stats"]["open_cases"] == 0
    assert body["stats"]["new_alerts_24h"] == 0
    assert body["open_alerts_total"] == 0
    assert body["triage_queue"] == []
    # Severity breakdown is always the four levels, highest first.
    assert [p["label"] for p in body["alerts_by_severity"]] == [
        "critical",
        "high",
        "medium",
        "low",
    ]
    # Pipeline is grouped by the status stage (statuses now carry an explicit
    # open vs in_progress stage).
    assert [p["label"] for p in body["case_pipeline"]] == [
        "Open",
        "In progress",
        "Resolved",
        "Duplicated",
    ]
    # 24 hourly ingestion buckets; 14 daily case-trend buckets by default.
    assert len(body["ingestion_24h"]) == 24
    assert body["trend_days"] == 14
    assert len(body["case_trend"]) == 14
    # No cases resolved / no alerts on an empty org.
    assert body["resolution_breakdown"] == []
    assert body["alerts_by_source"] == []


async def test_overview_trend_days_param(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.get("/api/v1/overview", params={"trend_days": 30}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["trend_days"] == 30
    assert len(r.json()["case_trend"]) == 30

    # Out-of-range values are rejected, not clamped.
    assert (
        await client.get("/api/v1/overview", params={"trend_days": 3}, headers=h)
    ).status_code == 422


async def test_overview_reflects_open_alerts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    # Two open alerts at different severities (freshly dated → within 24h).
    await client.post(
        "/api/v1/alerts/",
        json=_alert_payload(source_ref="a1", severity=4, title="Critical thing"),
        headers=h,
    )
    await client.post(
        "/api/v1/alerts/",
        json=_alert_payload(source_ref="a2", severity=3, title="High thing"),
        headers=h,
    )

    r = await client.get("/api/v1/overview", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["stats"]["new_alerts_24h"] == 2
    assert body["open_alerts_total"] == 2
    by_sev = {p["label"]: p["count"] for p in body["alerts_by_severity"]}
    assert by_sev["critical"] == 1
    assert by_sev["high"] == 1
    assert by_sev["medium"] == 0

    # Triage queue is severity-ordered: the critical alert comes first.
    titles = [a["title"] for a in body["triage_queue"]]
    assert titles == ["Critical thing", "High thing"]
    assert body["triage_queue"][0]["severity"] == 4

    # Both alerts share one source → a single feed bucket of 2.
    assert body["alerts_by_source"] == [{"label": "mail-gw", "count": 2}]


async def test_overview_requires_org_context(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    # No X-Organisation-Id header → cannot resolve an active org.
    r = await client.get(
        "/api/v1/overview",
        headers={"Authorization": f"Bearer {analyst_a_token}"},
    )
    assert r.status_code in (400, 401, 403, 422), r.text
