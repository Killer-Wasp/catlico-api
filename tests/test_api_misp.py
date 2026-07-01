"""F4: MISP routes — verify 501 stubs and server CRUD."""


async def test_misp_import_returns_501(client, org_a, admin_token):
    """MISP import returns 501 Not Implemented (after server creation)."""
    resp = await client.post(
        "/api/v1/misp/servers",
        json={"name": "test", "url": "https://misp.example.com", "auth_key": "key"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 201
    server_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/misp/servers/{server_id}/import-now",
        json={"server_id": server_id, "event_id": "123"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 501, resp.text


async def test_misp_test_connection_returns_501(client, org_a, admin_token):
    """MISP connection test returns 501 after server creation."""
    resp = await client.post(
        "/api/v1/misp/servers",
        json={"name": "test", "url": "https://misp.example.com", "auth_key": "key"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 201
    server_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/misp/servers/{server_id}/test",
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 501, resp.text


async def test_misp_server_crud_works(client, org_a, admin_token):
    """MISP server CRUD is not affected by 501 stubs."""
    resp = await client.post(
        "/api/v1/misp/servers",
        json={"name": "test", "url": "https://misp.example.com", "auth_key": "key"},
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 201

    resp = await client.get(
        "/api/v1/misp/servers",
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_misp_nonexistent_server_returns_404(client, org_a, admin_token):
    """Operations on nonexistent MISP servers return 404."""
    import uuid

    resp = await client.post(
        f"/api/v1/misp/servers/{uuid.uuid4()}/test",
        headers={"Authorization": f"Bearer {admin_token}", "X-Organisation-Id": org_a.id},
    )
    assert resp.status_code == 404
