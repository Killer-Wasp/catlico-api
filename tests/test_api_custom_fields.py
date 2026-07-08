"""Tests for Custom Fields (definitions + per-case/alert typed values)."""
import pytest
from httpx import AsyncClient

from app.core.security import TokenPayload, create_access_token
from app.crud import case_ as case_crud
from app.crud.organisation_member import add_member
from app.crud.user import create_user
from app.models.case_ import CaseCreate
from app.models.organisation_member import OrganisationMemberCreate
from app.models.user import UserCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user, title="c"):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


@pytest.fixture
async def plain_analyst_a(session, org_a, builtin_roles, admin_user):
    """A user with the *analyst* role in org-a: has write:case but NOT write:custom_field."""
    user = await create_user(
        session, UserCreate(first_name="Test", last_name="User", email="plain-analyst-a@test.com", password="password123")
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["analyst"].id),
        created_by=str(admin_user.id),
    )
    return user


@pytest.fixture
def plain_analyst_a_token(plain_analyst_a, org_a):
    return create_access_token(
        TokenPayload(
            user_id=plain_analyst_a.id, is_superadmin=False, organisations=[org_a.id]
        )
    )


async def _make_def(client, headers, **body):
    r = await client.post("/api/v1/custom-fields/", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- Definitions ---

async def test_definition_crud(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    d = await _make_def(
        client, h,
        name="business-unit", display_name="Business Unit",
        field_type="string", options=["finance", "it"],
    )
    assert d["options"] == ["finance", "it"]

    lst = await client.get("/api/v1/custom-fields/", headers=h)
    assert lst.json()["total"] == 1

    upd = await client.patch(
        f"/api/v1/custom-fields/{d['id']}",
        json={"display_name": "BU", "options": ["finance", "it", "ops"]},
        headers=h,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["display_name"] == "BU"
    assert upd.json()["options"] == ["finance", "it", "ops"]

    dele = await client.delete(f"/api/v1/custom-fields/{d['id']}", headers=h)
    assert dele.status_code == 204
    assert (await client.get("/api/v1/custom-fields/", headers=h)).json()["total"] == 0


async def test_duplicate_name_conflicts(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="sla", field_type="date")
    r = await client.post(
        "/api/v1/custom-fields/", json={"name": "sla", "field_type": "date"}, headers=h
    )
    assert r.status_code == 409


async def test_options_only_for_string(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        "/api/v1/custom-fields/",
        json={"name": "bad", "field_type": "integer", "options": ["1"]},
        headers=h,
    )
    assert r.status_code == 422


async def test_definitions_org_scoped(
    client: AsyncClient, org_a, org_b, builtin_roles,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    d = await _make_def(client, ha, name="secret-field", field_type="string")
    hb = _headers(analyst_b_token, org_b.id)
    # org-b sees none of org-a's definitions, and can't fetch/patch the id.
    assert (await client.get("/api/v1/custom-fields/", headers=hb)).json()["total"] == 0
    assert (
        await client.patch(
            f"/api/v1/custom-fields/{d['id']}", json={"display_name": "x"}, headers=hb
        )
    ).status_code == 404


async def test_definition_write_gating(
    client: AsyncClient, org_a, builtin_roles,
    analyst_a, analyst_a_token,
    readonly_a, readonly_a_token,
    plain_analyst_a, plain_analyst_a_token,
):
    admin_h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, admin_h, name="bu", field_type="string")

    # read-only and analyst roles can READ definitions...
    for token in (readonly_a_token, plain_analyst_a_token):
        h = _headers(token, org_a.id)
        assert (await client.get("/api/v1/custom-fields/", headers=h)).json()["total"] == 1
        # ...but cannot create them (no write:custom_field).
        r = await client.post(
            "/api/v1/custom-fields/", json={"name": "x", "field_type": "string"}, headers=h
        )
        assert r.status_code == 403, r.text


# --- Values ---

async def test_typed_value_round_trip(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="bu", field_type="string", options=["finance", "it"])
    await _make_def(client, h, name="impact", field_type="integer")
    await _make_def(client, h, name="score", field_type="float")
    await _make_def(client, h, name="exfil", field_type="boolean")
    await _make_def(client, h, name="sla", field_type="date")
    case = await _make_case(session, org_a, builtin_roles, analyst_a)

    r = await client.put(
        f"/api/v1/cases/{case.id}/custom-fields",
        json={"values": {
            "bu": "finance", "impact": 5, "score": 1.5,
            "exfil": True, "sla": "2026-06-14",
        }},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bu"] == "finance"
    assert body["impact"] == 5
    assert body["score"] == 1.5
    assert body["exfil"] is True
    assert body["sla"].startswith("2026-06-14")

    got = await client.get(f"/api/v1/cases/{case.id}/custom-fields", headers=h)
    assert got.json()["impact"] == 5


async def test_value_validation(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="bu", field_type="string", options=["finance"])
    await _make_def(client, h, name="impact", field_type="integer")
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    url = f"/api/v1/cases/{case.id}/custom-fields"

    # wrong type
    assert (await client.put(url, json={"values": {"impact": "nope"}}, headers=h)).status_code == 422
    # not an allowed option
    assert (await client.put(url, json={"values": {"bu": "legal"}}, headers=h)).status_code == 422
    # unknown field name
    assert (await client.put(url, json={"values": {"ghost": 1}}, headers=h)).status_code == 422


async def test_value_replace_semantics(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="a", field_type="integer")
    await _make_def(client, h, name="b", field_type="integer")
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    url = f"/api/v1/cases/{case.id}/custom-fields"

    await client.put(url, json={"values": {"a": 1, "b": 2}}, headers=h)
    # A second PUT omitting `b` clears it; null also clears.
    r = await client.put(url, json={"values": {"a": 9}}, headers=h)
    assert r.json() == {"a": 9}


async def test_custom_fields_in_case_public(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="impact", field_type="integer")
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    await client.put(
        f"/api/v1/cases/{case.id}/custom-fields", json={"values": {"impact": 7}}, headers=h
    )

    detail = await client.get(f"/api/v1/cases/{case.id}", headers=h)
    assert detail.json()["custom_fields"] == {"impact": 7}

    lst = await client.get("/api/v1/cases/", headers=h)
    item = next(c for c in lst.json()["items"] if c["id"] == case.id)
    assert item["custom_fields"] == {"impact": 7}


# --- Alerts ---

async def _make_alert(client, headers, source_ref="r1"):
    r = await client.post(
        "/api/v1/alerts/",
        json={"type": "ids", "source": "suricata", "source_ref": source_ref, "title": "hit"},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_alert_values_and_public(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="vendor", field_type="string")
    alert_id = await _make_alert(client, h)

    r = await client.put(
        f"/api/v1/alerts/{alert_id}/custom-fields",
        json={"values": {"vendor": "acme"}}, headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"vendor": "acme"}

    detail = await client.get(f"/api/v1/alerts/{alert_id}", headers=h)
    assert detail.json()["custom_fields"] == {"vendor": "acme"}


async def test_promotion_carries_custom_fields(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = _headers(analyst_a_token, org_a.id)
    await _make_def(client, h, name="vendor", field_type="string")
    await _make_def(client, h, name="impact", field_type="integer")
    alert_id = await _make_alert(client, h)
    await client.put(
        f"/api/v1/alerts/{alert_id}/custom-fields",
        json={"values": {"vendor": "acme", "impact": 3}}, headers=h,
    )

    promo = await client.post(f"/api/v1/alerts/{alert_id}/promote", json={}, headers=h)
    assert promo.status_code == 201, promo.text
    case_id = promo.json()["id"]

    got = await client.get(f"/api/v1/cases/{case_id}/custom-fields", headers=h)
    assert got.json() == {"impact": 3, "vendor": "acme"}
