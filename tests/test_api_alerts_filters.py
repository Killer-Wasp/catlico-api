"""Server-side clause filtering + facets for GET /alerts.

Filters are `filter=key~op~value` terms (keys: severity, source, tlp, alert,
title, and `tag:<group-key>`): OR within a key, AND across keys."""

from httpx import AsyncClient

from app.crud import alert as alert_crud
from app.crud import tag as tag_crud
from app.models.alert import AlertCreate
from app.models.tag import TaggableType


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _seed_alert(
    session,
    org,
    created_by,
    *,
    source_ref,
    source="mail-gw",
    severity=2,
    tlp=2,
    title="t",
    tags=None,
):
    alert, _ = await alert_crud.ingest_alert(
        session,
        AlertCreate(
            type="phishing",
            source=source,
            source_ref=source_ref,
            title=title,
            severity=severity,
            tlp=tlp,
        ),
        organisation_id=org.id,
        created_by=str(created_by),
    )
    if tags:
        await tag_crud.set_tags(session, TaggableType.alert, str(alert.id), tags)
    return alert


def _f(key, op, value):
    return f"{key}~{op}~{value}"


async def _list(client, token, org, params=None):
    resp = await client.get(
        "/api/v1/alerts/", params=params or {}, headers=_headers(token, org.id)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_filter_alerts_by_severity(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_alert(session, org_a, analyst_a.id, source_ref="a1", title="low", severity=2)
    await _seed_alert(session, org_a, analyst_a.id, source_ref="a2", title="crit", severity=4)

    body = await _list(client, analyst_a_token, org_a, {"filter": [_f("severity", "eq", "4")]})
    assert {a["title"] for a in body["items"]} == {"crit"}


async def test_filter_alerts_by_source_and_tlp(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_alert(
        session, org_a, analyst_a.id, source_ref="b1", title="edr", source="EDR", tlp=3
    )
    await _seed_alert(
        session, org_a, analyst_a.id, source_ref="b2", title="mail", source="mail-gw", tlp=1
    )

    by_source = await _list(client, analyst_a_token, org_a, {"filter": [_f("source", "eq", "EDR")]})
    assert {a["title"] for a in by_source["items"]} == {"edr"}

    by_tlp = await _list(client, analyst_a_token, org_a, {"filter": [_f("tlp", "eq", "3")]})
    assert {a["title"] for a in by_tlp["items"]} == {"edr"}


async def test_filter_alerts_by_title_contains(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_alert(session, org_a, analyst_a.id, source_ref="c1", title="Ransomware staging")
    await _seed_alert(session, org_a, analyst_a.id, source_ref="c2", title="Benign login")

    body = await _list(client, analyst_a_token, org_a, {"filter": [_f("title", "co", "ransom")]})
    assert {a["title"] for a in body["items"]} == {"Ransomware staging"}


async def test_filter_alerts_by_tag_or_within_key(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_alert(
        session, org_a, analyst_a.id, source_ref="d1", title="amber", tags=["tlp:amber"]
    )
    await _seed_alert(
        session, org_a, analyst_a.id, source_ref="d2", title="red", tags=["tlp:red"]
    )
    await _seed_alert(session, org_a, analyst_a.id, source_ref="d3", title="untagged")

    body = await _list(
        client,
        analyst_a_token,
        org_a,
        {"filter": [_f("tag:tlp", "eq", "amber"), _f("tag:tlp", "eq", "red")]},
    )
    assert {a["title"] for a in body["items"]} == {"amber", "red"}


async def test_alert_facets(
    client: AsyncClient, session, org_a, analyst_a, analyst_a_token
):
    await _seed_alert(
        session, org_a, analyst_a.id, source_ref="e1", source="EDR", tags=["tlp:amber", "phishing"]
    )
    await _seed_alert(session, org_a, analyst_a.id, source_ref="e2", source="mail-gw")

    resp = await client.get("/api/v1/alerts/filters", headers=_headers(analyst_a_token, org_a.id))
    assert resp.status_code == 200, resp.text
    facets = resp.json()
    assert facets["sources"] == ["EDR", "mail-gw"]
    assert facets["tag_keys"] == {"tlp": ["amber"]}
    # Free tags are not filterable.
    assert "phishing" not in facets["tag_keys"]
