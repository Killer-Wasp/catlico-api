"""Connector catalog: analyzer registration (shared-secret auth), per-org enable/
disable, super-admin global config with secret redaction."""
from httpx import AsyncClient

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


async def test_register_requires_valid_secret(client: AsyncClient, analyzer_secret):
    body = {"connectors": [GEOIP]}
    assert (await client.post("/api/v1/analyzer/register", json=body)).status_code == 401
    bad = await client.post(
        "/api/v1/analyzer/register", json=body, headers=_analyzer_h("nope")
    )
    assert bad.status_code == 401
    ok = await _register(client, analyzer_secret, GEOIP)
    assert ok.status_code == 200, ok.text
    assert ok.json()["registered"] == ["geoip2"]


async def test_catalog_lists_with_per_org_enabled(
    client: AsyncClient, analyzer_secret, org_a, builtin_roles, analyst_a, analyst_a_token
):
    await _register(client, analyzer_secret, GEOIP)
    h = _h(analyst_a_token, org_a.id)

    lst = await client.get("/api/v1/connectors", headers=h)
    assert lst.status_code == 200, lst.text
    assert lst.json()[0]["name"] == "geoip2"
    assert lst.json()[0]["enabled"] is False

    en = await client.post("/api/v1/connectors/geoip2/enable", headers=h)
    assert en.status_code == 200
    assert en.json()["enabled"] is True

    assert (await client.get("/api/v1/connectors/geoip2", headers=h)).json()["enabled"] is True

    dis = await client.post("/api/v1/connectors/geoip2/disable", headers=h)
    assert dis.json()["enabled"] is False


async def test_enable_requires_write_connector(
    client: AsyncClient, analyzer_secret, org_a, builtin_roles, readonly_a, readonly_a_token
):
    await _register(client, analyzer_secret, GEOIP)
    h = _h(readonly_a_token, org_a.id)
    # read-only can see the catalog but not toggle it.
    assert (await client.get("/api/v1/connectors", headers=h)).status_code == 200
    assert (await client.post("/api/v1/connectors/geoip2/enable", headers=h)).status_code == 403


async def test_superadmin_config_redacts_secrets(
    client: AsyncClient, analyzer_secret, admin_token, org_a, builtin_roles,
    analyst_a, analyst_a_token,
):
    await _register(client, analyzer_secret, GEOIP)

    # Non-superadmin cannot set global config.
    forbidden = await client.put(
        "/api/v1/connectors/geoip2/config",
        json={"settings": {"region": "eu"}, "secrets": {"api_key": "s3cr3t"}},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert forbidden.status_code == 403

    saved = await client.put(
        "/api/v1/connectors/geoip2/config",
        json={"settings": {"region": "eu"}, "secrets": {"api_key": "s3cr3t"}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["has_secrets"] is True
    assert body["settings"] == {"region": "eu"}
    assert "s3cr3t" not in saved.text  # secret never echoed

    # And it stays redacted in the org-facing catalog.
    got = await client.get("/api/v1/connectors/geoip2", headers=_h(analyst_a_token, org_a.id))
    assert "s3cr3t" not in got.text
    assert got.json()["settings"] == {"region": "eu"}


async def test_config_unknown_connector_404(client: AsyncClient, admin_token):
    r = await client.put(
        "/api/v1/connectors/ghost/config",
        json={"settings": {}, "secrets": {}},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 404
