"""STIX enterprise-attack bundle parsing (no network)."""
import httpx
import pytest
from httpx import AsyncClient

from app.services.attack_import import parse_attack_bundle

# Trimmed to the fields the parser reads. Real bundles carry ~40MB of objects.
FIXTURE_BUNDLE = {
    "type": "bundle",
    "objects": [
        {   # technique in two tactics
            "type": "attack-pattern",
            "name": "Valid Accounts",
            "description": "Adversaries may obtain and abuse credentials.",
            "external_references": [
                {"source_name": "mitre-attack", "external_id": "T1078",
                 "url": "https://attack.mitre.org/techniques/T1078"},
                {"source_name": "capec", "external_id": "CAPEC-560"},
            ],
            "kill_chain_phases": [
                {"kill_chain_name": "mitre-attack", "phase_name": "defense-evasion"},
                {"kill_chain_name": "mitre-attack", "phase_name": "persistence"},
                {"kill_chain_name": "other-chain", "phase_name": "ignored"},
            ],
        },
        {   # sub-technique -> parent derived from the id prefix
            "type": "attack-pattern",
            "name": "Default Accounts",
            "description": "",
            "x_mitre_is_subtechnique": True,
            "external_references": [
                {"source_name": "mitre-attack", "external_id": "T1078.001",
                 "url": "https://attack.mitre.org/techniques/T1078/001"},
            ],
            "kill_chain_phases": [
                {"kill_chain_name": "mitre-attack", "phase_name": "persistence"},
            ],
        },
        {   # revoked -> skipped
            "type": "attack-pattern",
            "name": "Old Technique",
            "revoked": True,
            "external_references": [
                {"source_name": "mitre-attack", "external_id": "T9998"},
            ],
        },
        {   # deprecated -> skipped
            "type": "attack-pattern",
            "name": "Deprecated Technique",
            "x_mitre_deprecated": True,
            "external_references": [
                {"source_name": "mitre-attack", "external_id": "T9999"},
            ],
        },
        {   # no mitre-attack reference -> skipped
            "type": "attack-pattern",
            "name": "Unidentifiable",
            "external_references": [{"source_name": "capec", "external_id": "CAPEC-1"}],
        },
        {"type": "intrusion-set", "name": "Not a technique"},
    ],
}


def test_parses_techniques_with_tactics_and_url():
    items = parse_attack_bundle(FIXTURE_BUNDLE)
    by_id = {i.external_id: i for i in items}
    assert set(by_id) == {"T1078", "T1078.001"}
    t = by_id["T1078"]
    assert t.name == "Valid Accounts"
    assert t.tactics == ["defense-evasion", "persistence"]
    assert t.url == "https://attack.mitre.org/techniques/T1078"
    assert t.parent_external_id is None


def test_subtechnique_gets_parent_from_id_prefix():
    items = parse_attack_bundle(FIXTURE_BUNDLE)
    sub = next(i for i in items if i.external_id == "T1078.001")
    assert sub.parent_external_id == "T1078"


def test_skips_revoked_deprecated_and_unidentifiable():
    ids = {i.external_id for i in parse_attack_bundle(FIXTURE_BUNDLE)}
    assert "T9998" not in ids
    assert "T9999" not in ids
    assert "CAPEC-1" not in ids


def test_missing_optional_fields_fall_back():
    bundle = {
        "objects": [
            {
                "type": "attack-pattern",
                "external_references": [
                    {"source_name": "mitre-attack", "external_id": "T5000"},
                ],
            },
        ],
    }
    (item,) = parse_attack_bundle(bundle)
    assert item.name == "T5000"
    assert item.description == ""
    assert item.url == ""
    assert item.tactics == []
    assert item.parent_external_id is None


def test_empty_bundle_parses_to_nothing():
    assert parse_attack_bundle({"objects": []}) == []
    assert parse_attack_bundle({}) == []


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


@pytest.fixture
def fake_bundle(monkeypatch):
    async def _fake():
        return FIXTURE_BUNDLE
    monkeypatch.setattr("app.services.attack_import.fetch_attack_bundle", _fake)


async def test_import_attack_upserts_catalog(
    client: AsyncClient, org_a, admin_user, admin_token, fake_bundle
):
    h = _headers(admin_token, org_a.id)
    r = await client.post("/api/v1/patterns/import-attack", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"created": 2, "updated": 0, "total": 2}

    # Re-import: idempotent upsert, nothing duplicated.
    r2 = await client.post("/api/v1/patterns/import-attack", headers=h)
    assert r2.json() == {"created": 0, "updated": 2, "total": 2}

    listed = (await client.get("/api/v1/patterns", headers=h)).json()
    assert listed["total"] == 2
    by_id = {p["external_id"]: p for p in listed["items"]}
    assert by_id["T1078"]["tactics"] == ["defense-evasion", "persistence"]
    assert by_id["T1078.001"]["parent_external_id"] == "T1078"


async def test_import_attack_requires_org_admin(
    client: AsyncClient, org_a, readonly_a, readonly_a_token, fake_bundle
):
    h = _headers(readonly_a_token, org_a.id)
    r = await client.post("/api/v1/patterns/import-attack", headers=h)
    assert r.status_code == 403, r.text


async def test_import_attack_fetch_failure_returns_502(
    client: AsyncClient, org_a, admin_user, admin_token, monkeypatch
):
    async def _boom():
        raise httpx.ConnectError("no route to github")
    monkeypatch.setattr("app.services.attack_import.fetch_attack_bundle", _boom)
    h = _headers(admin_token, org_a.id)
    r = await client.post("/api/v1/patterns/import-attack", headers=h)
    assert r.status_code == 502, r.text
