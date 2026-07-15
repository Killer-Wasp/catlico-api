"""API tests for Phase 1-4 blocked-feature routes."""
import pytest
from httpx import AsyncClient


def _h(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


# ── Phase 1: Tags (Taxonomies) ──────────────────────────────────────────────

async def test_tag_create(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/tags/",
        json={"namespace": "tlp", "predicate": "red", "description": "TLP Red", "colour": "#ff0000"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["namespace"] == "tlp"
    assert r.json()["predicate"] == "red"
    assert r.json()["description"] == "TLP Red"
    assert r.json()["colour"] == "#ff0000"
    assert r.json()["tag"] == "tlp:red"


async def test_tag_create_duplicate_rejected(client: AsyncClient, admin_token):
    await client.post(
        "/api/v1/tags/",
        json={"predicate": "duptag"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.post(
        "/api/v1/tags/",
        json={"predicate": "duptag"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 409


async def test_tag_create_rejected_for_non_admin(client: AsyncClient, analyst_a_token, org_a):
    r = await client.post(
        "/api/v1/tags/",
        json={"predicate": "nope"},
        headers=_h(analyst_a_token, org_a.id),
    )
    assert r.status_code == 403


async def test_tag_update(client: AsyncClient, admin_token):
    r = await client.post(
        "/api/v1/tags/",
        json={"predicate": "updateme", "description": "old", "colour": "#111111"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    tag_id = r.json()["id"]
    r = await client.patch(
        f"/api/v1/tags/{tag_id}",
        json={"description": "new desc", "colour": "#f00baa"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "new desc"
    assert r.json()["colour"] == "#f00baa"


async def test_tag_list_includes_description(client: AsyncClient, admin_token):
    await client.post(
        "/api/v1/tags/",
        json={"predicate": "listable", "description": "A test tag", "colour": "#abc"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.get(
        "/api/v1/tags/", headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200
    found = [t for t in r.json() if t["predicate"] == "listable"]
    assert len(found) == 1
    assert found[0]["description"] == "A test tag"
    assert found[0]["colour"] == "#abc"


# ── Phase 2: API Keys ───────────────────────────────────────────────────────

async def test_api_key_crud(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/api-keys/",
        json={"name": "my-key"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["name"] == "my-key"
    assert data["prefix"] == "thp_"
    assert len(data["key"]) > 40  # plaintext token returned once
    key_id = data["id"]

    lst = await client.get("/api/v1/api-keys/", headers=h)
    assert lst.status_code == 200
    assert any(k["id"] == key_id for k in lst.json())

    r = await client.patch(
        f"/api/v1/api-keys/{key_id}",
        json={"name": "renamed"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"

    r = await client.delete(f"/api/v1/api-keys/{key_id}", headers=h)
    assert r.status_code == 204

    lst = await client.get("/api/v1/api-keys/", headers=h)
    assert not any(k["id"] == key_id for k in lst.json())


async def test_api_key_org_scoped(client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token):
    ha = _h(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/api-keys/", json={"name": "org-a-key"}, headers=ha
    )
    key_id = r.json()["id"]

    hb = _h(analyst_b_token, org_b.id)
    r = await client.get(f"/api/v1/api-keys/{key_id}", headers=hb)
    assert r.status_code == 404  # not in org-b's scope


async def test_api_key_read_enforces_org_admin(
    client: AsyncClient, session, org_a, builtin_roles, admin_user,
):
    """A read-only user cannot list or view API keys."""
    from app.core.security import TokenPayload, create_access_token
    from app.crud.organisation_member import add_member
    from app.crud.user import create_user
    from app.models.organisation_member import OrganisationMemberCreate
    from app.models.user import UserCreate

    reader = await create_user(
        session, UserCreate(first_name="Test", last_name="User", email="api-reader@test.com", password="password123")
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(
            user_id=reader.id, role_id=builtin_roles["read-only"].id
        ),
        created_by=str(admin_user.id),
    )
    reader_token = create_access_token(
        TokenPayload(
            user_id=reader.id, is_superadmin=False, organisations=[org_a.id]
        )
    )
    h = _h(reader_token, org_a.id)

    r = await client.get("/api/v1/api-keys/", headers=h)
    assert r.status_code == 403, r.text

    # Create a key as org admin first
    admin_h = _h(
        create_access_token(
            TokenPayload(
                user_id=admin_user.id, is_superadmin=True, organisations=[]
            )
        ),
        org_a.id,
    )
    r = await client.post(
        "/api/v1/api-keys/", json={"name": "admin-key"}, headers=admin_h
    )
    key_id = r.json()["id"]

    r = await client.get(f"/api/v1/api-keys/{key_id}", headers=h)
    assert r.status_code == 403, r.text


# ── Phase 2: SLA ────────────────────────────────────────────────────────────

async def test_sla_crud(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    r = await client.put(
        "/api/v1/sla-policies/",
        json=[
            {"severity": 2, "ack_seconds": 900, "resolve_seconds": 14400, "escalation_target": "Lead", "enabled": True},
            {"severity": 4, "ack_seconds": 300, "resolve_seconds": 3600, "enabled": True},
        ],
        headers=h,
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    lst = await client.get("/api/v1/sla-policies/", headers=h)
    assert lst.status_code == 200
    assert lst.json()["total"] == 2

    # Upsert: same severity overwrites
    r = await client.put(
        "/api/v1/sla-policies/",
        json=[{"severity": 2, "ack_seconds": 600, "resolve_seconds": 7200, "enabled": False}],
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()[0]["ack_seconds"] == 600
    assert r.json()[0]["enabled"] is False

    lst = await client.get("/api/v1/sla-policies/", headers=h)
    assert lst.json()["total"] == 2  # still 2, upsert not insert


async def test_sla_org_scoped(client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token):
    ha = _h(analyst_a_token, org_a.id)
    await client.put(
        "/api/v1/sla-policies/",
        json=[{"severity": 1, "ack_seconds": 60, "resolve_seconds": 120}],
        headers=ha,
    )
    hb = _h(analyst_b_token, org_b.id)
    lst = await client.get("/api/v1/sla-policies/", headers=hb)
    assert lst.json()["total"] == 0


async def test_sla_delete(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    await client.put(
        "/api/v1/sla-policies/",
        json=[
            {"severity": 2, "ack_seconds": 900, "resolve_seconds": 14400},
            {"severity": 4, "ack_seconds": 300, "resolve_seconds": 3600},
        ],
        headers=h,
    )
    policies = (await client.get("/api/v1/sla-policies/", headers=h)).json()["items"]
    target = next(p for p in policies if p["severity"] == 2)

    deleted = await client.delete(f"/api/v1/sla-policies/{target['id']}", headers=h)
    assert deleted.status_code == 204, deleted.text

    remaining = (await client.get("/api/v1/sla-policies/", headers=h)).json()
    assert remaining["total"] == 1
    assert remaining["items"][0]["severity"] == 4

    # Deleting an unknown id 404s.
    missing = await client.delete("/api/v1/sla-policies/999999", headers=h)
    assert missing.status_code == 404


async def test_sla_delete_is_org_scoped(
    client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token
):
    ha = _h(analyst_a_token, org_a.id)
    await client.put(
        "/api/v1/sla-policies/",
        json=[{"severity": 1, "ack_seconds": 60, "resolve_seconds": 120}],
        headers=ha,
    )
    policy_id = (await client.get("/api/v1/sla-policies/", headers=ha)).json()["items"][0]["id"]

    hb = _h(analyst_b_token, org_b.id)
    # Org B cannot delete org A's policy — it resolves to 404, not a silent delete.
    cross = await client.delete(f"/api/v1/sla-policies/{policy_id}", headers=hb)
    assert cross.status_code == 404
    still_there = (await client.get("/api/v1/sla-policies/", headers=ha)).json()
    assert still_there["total"] == 1


# ── Phase 2: Knowledge Base ─────────────────────────────────────────────────

async def test_kb_crud(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/knowledge-base/",
        json={
            "title": "Phishing Runbook",
            "summary": "How to handle phishing",
            "tags": ["phishing", "runbook"],
            "content": "## Triage\n\nCheck headers",
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    page_id = r.json()["id"]
    assert r.json()["title"] == "Phishing Runbook"
    assert r.json()["tags"] == ["phishing", "runbook"]
    assert r.json()["content"] == "## Triage\n\nCheck headers"

    lst = await client.get("/api/v1/knowledge-base/", headers=h)
    assert lst.json()["total"] == 1

    r = await client.get("/api/v1/knowledge-base/?search=phish", headers=h)
    assert r.json()["total"] == 1

    r = await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"title": "Updated Runbook", "content": "Updated steps"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Updated Runbook"
    assert r.json()["content"] == "Updated steps"

    r = await client.delete(f"/api/v1/knowledge-base/{page_id}", headers=h)
    assert r.status_code == 204

    lst = await client.get("/api/v1/knowledge-base/", headers=h)
    assert lst.json()["total"] == 0


async def test_kb_versions_list_and_revert(client: AsyncClient, org_a, analyst_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Runbook", "summary": "v1", "tags": ["one"], "content": "first"},
        headers=h,
    )
    assert created.status_code == 201, created.text
    page_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/knowledge-base/{page_id}",
        json={"summary": "v2", "tags": ["two"], "content": "second"},
        headers=h,
    )
    assert updated.status_code == 200, updated.text

    history = await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)
    assert history.status_code == 200, history.text
    versions = history.json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["action"] == "update"
    assert versions[0]["changed_fields"] == ["summary", "tags", "content"]
    assert versions[0]["edited_by_email"] == analyst_a.email

    create_version = next(v for v in versions if v["action"] == "create")
    reverted = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{create_version['id']}/revert",
        headers=h,
    )
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["summary"] == "v1"
    assert reverted.json()["tags"] == ["one"]
    assert reverted.json()["content"] == "first"

    reverted_history = (
        await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=h)
    ).json()
    assert reverted_history[0]["action"] == "revert"
    assert reverted_history[0]["reverted_from_version_id"] == create_version["id"]


async def test_kb_version_access_requires_manage_org(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token,
    readonly_a, readonly_a_token,
):
    """Finding 3: read:knowledge_base / write:knowledge_base now live only inside
    manage:org, so the old view-vs-revert split for KB collapsed. A read-only member
    can no longer view versions; a manage:org member (analyst) can view AND revert."""
    writer_h = _h(analyst_a_token, org_a.id)
    reader_h = _h(readonly_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Protected", "content": "current"},
        headers=writer_h,
    )
    page_id = created.json()["id"]

    # read-only can no longer view versions (no read:knowledge_base).
    denied = await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=reader_h)
    assert denied.status_code == 403, denied.text

    # a manage:org member views and reverts.
    versions = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=writer_h)).json()
    assert len(versions) == 1
    reverted = await client.post(
        f"/api/v1/knowledge-base/{page_id}/versions/{versions[0]['id']}/revert",
        headers=writer_h,
    )
    assert reverted.status_code == 200, reverted.text


async def test_kb_versions_are_org_scoped(
    client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token
):
    ha = _h(analyst_a_token, org_a.id)
    hb = _h(analyst_b_token, org_b.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Org A page", "content": "private"},
        headers=ha,
    )
    page_id = created.json()["id"]
    version_id = (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=ha)).json()[0]["id"]

    assert (await client.get(f"/api/v1/knowledge-base/{page_id}/versions", headers=hb)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/knowledge-base/{page_id}/versions/{version_id}/revert",
            headers=hb,
        )
    ).status_code == 404


async def test_kb_export_and_import_json_document(
    client: AsyncClient, org_a, analyst_a_token
):
    h = _h(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={
            "title": "Exported",
            "summary": "Portable",
            "tags": ["portable"],
            "content": "Carry me",
        },
        headers=h,
    )
    page_id = created.json()["id"]

    exported = await client.get(f"/api/v1/knowledge-base/{page_id}/export", headers=h)
    assert exported.status_code == 200, exported.text
    document = exported.json()
    assert document["page"]["title"] == "Exported"
    assert document["versions"][0]["action"] == "create"

    imported = await client.post(
        "/api/v1/knowledge-base/import",
        json=document,
        headers=h,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["id"] != page_id
    assert imported.json()["title"] == "Exported"
    assert imported.json()["content"] == "Carry me"

    imported_history = await client.get(
        f"/api/v1/knowledge-base/{imported.json()['id']}/versions",
        headers=h,
    )
    assert imported_history.json()[0]["action"] == "import"


async def test_kb_cross_org_cannot_export(
    client: AsyncClient, org_a, org_b, analyst_a_token, analyst_b_token
):
    ha = _h(analyst_a_token, org_a.id)
    hb = _h(analyst_b_token, org_b.id)
    created = await client.post(
        "/api/v1/knowledge-base/",
        json={"title": "Org A", "content": "private"},
        headers=ha,
    )
    page_id = created.json()["id"]

    export = await client.get(f"/api/v1/knowledge-base/{page_id}/export", headers=hb)
    assert export.status_code == 404


async def test_kb_readonly_user_cannot_import(
    client: AsyncClient, org_a, readonly_a_token
):
    reader_h = _h(readonly_a_token, org_a.id)
    imported = await client.post(
        "/api/v1/knowledge-base/import",
        json={
            "page": {"title": "Reader import", "content": "blocked"},
            "versions": [],
        },
        headers=reader_h,
    )
    assert imported.status_code == 403


# ── Phase 3: Notifiers ──────────────────────────────────────────────────────

async def test_notifier_crud(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/notifications/notifiers/",
        json={
            "type": "slack",
            "target": "#soc-alerts",
            "config": {"icon_emoji": ":warning:"},
            "secrets": {"bot_token": "xoxb-secret"},
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    n_id = r.json()["id"]
    assert r.json()["type"] == "slack"
    assert r.json()["target"] == "#soc-alerts"
    assert r.json()["has_secrets"] is True
    # secrets must never be in the public response
    assert "secrets" not in r.json()
    assert "secrets_encrypted" not in r.json()

    lst = await client.get("/api/v1/notifications/notifiers/", headers=h)
    assert lst.json()["total"] == 1
    assert lst.json()["items"][0]["has_secrets"] is True

    # settings-only update preserves secrets
    r = await client.patch(
        f"/api/v1/notifications/notifiers/{n_id}",
        json={"enabled": False},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["has_secrets"] is True  # secrets preserved

    r = await client.delete(f"/api/v1/notifications/notifiers/{n_id}", headers=h)
    assert r.status_code == 204

    lst = await client.get("/api/v1/notifications/notifiers/", headers=h)
    assert lst.json()["total"] == 0


# ── Phase 3: Notification Rules ─────────────────────────────────────────────

async def test_notification_rule_crud(client: AsyncClient, org_a, analyst_a_token):
    h = _h(analyst_a_token, org_a.id)
    # Create a notifier first so notifier_ids can reference it
    n = await client.post(
        "/api/v1/notifications/notifiers/",
        json={"type": "email", "target": "soc@org.test"},
        headers=h,
    )
    n_id = n.json()["id"]

    r = await client.post(
        "/api/v1/notifications/notification-rules/",
        json={
            "name": "Critical Alerts",
            "event": "alert.critical",
            "notifier_ids": [n_id],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["id"]
    assert r.json()["event"] == "alert.critical"
    assert r.json()["notifier_ids"] == [n_id]

    lst = await client.get("/api/v1/notifications/notification-rules/", headers=h)
    assert lst.json()["total"] == 1

    r = await client.patch(
        f"/api/v1/notifications/notification-rules/{rule_id}",
        json={"enabled": False},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False

    r = await client.delete(f"/api/v1/notifications/notification-rules/{rule_id}", headers=h)
    assert r.status_code == 204


# ── Organisation profile ─────────────────────────────────────────────────────

async def test_org_profile_persist_timezone_tlp(
    client: AsyncClient, session, org_a, analyst_a_token,
):
    """Organisation profile PATCH persists timezone and default_tlp."""
    h = _h(analyst_a_token, org_a.id)

    r = await client.patch(
        f"/api/v1/organisations/{org_a.id}",
        json={"timezone": "Australia/Sydney", "default_tlp": 3},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["timezone"] == "Australia/Sydney"
    assert data["default_tlp"] == 3

    r = await client.get(f"/api/v1/organisations/{org_a.id}", headers=h)
    assert r.json()["timezone"] == "Australia/Sydney"
    assert r.json()["default_tlp"] == 3


async def test_org_profile_default_values(
    client: AsyncClient, session, org_a, analyst_a_token,
):
    """New orgs get UTC timezone and TLP:AMBER (2) by default."""
    h = _h(analyst_a_token, org_a.id)
    r = await client.get(f"/api/v1/organisations/{org_a.id}", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["timezone"] == "UTC"
    assert data["default_tlp"] == 2
