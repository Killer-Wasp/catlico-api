"""Tag create endpoint (POST /tags/) — the freetag "add" the settings UI uses."""

from httpx import AsyncClient


def _admin_h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_freetag(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/tags/",
        json={"predicate": "malware", "namespace": "", "value": ""},
        headers=_admin_h(admin_token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # A namespace-less tag renders as just its predicate.
    assert body["tag"] == "malware"
    assert body["namespace"] == ""
    assert body["value"] == ""

    # It now shows up in the global tag list.
    listed = (await client.get("/api/v1/tags/", headers=_admin_h(admin_token))).json()
    assert "malware" in {t["tag"] for t in listed}


async def test_create_duplicate_freetag_conflicts(client: AsyncClient, admin_token):
    body = {"predicate": "phishing", "namespace": "", "value": ""}
    first = await client.post("/api/v1/tags/", json=body, headers=_admin_h(admin_token))
    assert first.status_code == 201, first.text
    dup = await client.post("/api/v1/tags/", json=body, headers=_admin_h(admin_token))
    assert dup.status_code == 409


async def test_create_freetag_requires_superadmin(
    client: AsyncClient, viewer_token
):
    r = await client.post(
        "/api/v1/tags/",
        json={"predicate": "ransomware", "namespace": "", "value": ""},
        headers=_admin_h(viewer_token),
    )
    assert r.status_code == 403
