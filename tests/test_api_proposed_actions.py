"""Public proposed-action review: plugins propose canonical edits, analysts
approve/reject, approval applies through normal CRUD with a combined actor.
"""
import pytest
from httpx import AsyncClient

from app.core.security import TokenPayload, create_access_token
from app.crud.organisation_member import add_member
from app.crud.user import create_user
from app.models.organisation_member import OrganisationMemberCreate
from app.models.user import UserCreate

from tests.test_api_plugin_runtime import (
    _create_case_with_observable,
    _runtime_h,
    _runtime_token_for,
)

_RUNTIME_PREFIX = "/api/internal/plugin-runtime"
_ACTIONS = "/api/v1/proposed-actions"


def _user_h(token: str, org_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


@pytest.fixture
async def readonly_a(session, org_a, builtin_roles, admin_user):
    """A read-only member of org-a (has read:connector/read:case, not write:case)."""
    user = await create_user(
        session,
        UserCreate(
            first_name="Read", last_name="Only",
            email="readonly-a@test.com", password="password123",
        ),
    )
    await add_member(
        session,
        org_a.id,
        OrganisationMemberCreate(user_id=user.id, role_id=builtin_roles["read-only"].id),
        created_by=str(admin_user.id),
    )
    return user


@pytest.fixture
def readonly_a_token(readonly_a, org_a):
    return create_access_token(
        TokenPayload(user_id=readonly_a.id, is_superadmin=False, organisations=[org_a.id])
    )


async def _case_runtime_token(client, runner_secret, admin_token, org_a, case_id):
    return await _runtime_token_for(
        client,
        runner_secret,
        admin_token,
        org_a.id,
        event_type="case.created",
        event_object_type="case",
        event_object_id=str(case_id),
    )


async def _propose_case_patch(client, token, case_id, description):
    r = await client.patch(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        json={"description": description},
        headers=_runtime_h(token),
    )
    assert r.status_code == 202, r.text
    return r.json()["proposed_action_id"]


# --- Propose -> list -> approve applies ---


async def test_patch_case_propose_then_approve_applies(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    action_id = await _propose_case_patch(client, token, case_id, "Patched by plugin")

    h = _user_h(analyst_a_token, org_a.id)
    listed = await client.get(f"{_ACTIONS}?entity_type=case&entity_id={case_id}", headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert any(a["id"] == action_id and a["status"] == "proposed" for a in rows)

    approved = await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    case = await client.get(f"/api/v1/cases/{case_id}", headers=h)
    assert case.status_code == 200, case.text
    assert case.json()["description"] == "Patched by plugin"


async def test_add_tag_propose_then_approve_applies(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    r = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "plugin-flagged"},
        headers=_runtime_h(token),
    )
    assert r.status_code == 202, r.text
    action_id = r.json()["proposed_action_id"]

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert tags.status_code == 200, tags.text
    assert "plugin-flagged" in tags.json()


async def test_create_task_propose_then_approve_applies(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    r = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tasks",
        json={"title": "Investigate IP", "description": "check"},
        headers=_runtime_h(token),
    )
    assert r.status_code == 202, r.text
    action_id = r.json()["proposed_action_id"]

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    queue = await client.get("/api/v1/task-queue?limit=50", headers=h)
    assert queue.status_code == 200, queue.text
    titles = [t["title"] for t in queue.json()["items"]]
    assert "Investigate IP" in titles


# --- Reject / guards ---


async def test_reject_leaves_entity_unchanged(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    action_id = await _propose_case_patch(client, token, case_id, "Should not apply")

    h = _user_h(analyst_a_token, org_a.id)
    rejected = await client.post(
        f"{_ACTIONS}/{action_id}/reject",
        json={"reason": "not warranted"},
        headers=h,
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["decision_reason"] == "not warranted"

    case = await client.get(f"/api/v1/cases/{case_id}", headers=h)
    assert case.json()["description"] != "Should not apply"


async def test_approve_requires_write_permission(
    client: AsyncClient, org_a, analyst_a_token, readonly_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    action_id = await _propose_case_patch(client, token, case_id, "Needs write:case")

    # Read-only member can list but not approve.
    listed = await client.get(_ACTIONS, headers=_user_h(readonly_a_token, org_a.id))
    assert listed.status_code == 200, listed.text
    denied = await client.post(
        f"{_ACTIONS}/{action_id}/approve",
        headers=_user_h(readonly_a_token, org_a.id),
    )
    assert denied.status_code == 403, denied.text


async def test_double_decision_conflicts(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    action_id = await _propose_case_patch(client, token, case_id, "Once")

    h = _user_h(analyst_a_token, org_a.id)
    assert (await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)).status_code == 200
    second = await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)
    assert second.status_code == 409, second.text


async def test_auto_apply_policy_applies_low_risk_immediately(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    h = _user_h(analyst_a_token, org_a.id)
    # Token registers the runner and enables the plugin (OrgPlugin must exist
    # before an auto-apply policy can be set).
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    policy = await client.put(
        "/api/v1/plugins/acme-threatintel/auto-apply",
        json={"actions": ["add_tag"]},
        headers=h,
    )
    assert policy.status_code == 200, policy.text

    r = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "auto-tag"},
        headers=_runtime_h(token),
    )
    assert r.status_code == 202, r.text
    # Born applied, not proposed.
    assert r.json()["status"] == "applied"

    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert "auto-tag" in tags.json()


async def test_auto_apply_policy_rejects_high_impact(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    await _create_case_with_observable(client, org_a, analyst_a_token)
    h = _user_h(analyst_a_token, org_a.id)
    r = await client.put(
        "/api/v1/plugins/acme-threatintel/auto-apply",
        json={"actions": ["patch_case_description"]},
        headers=h,
    )
    assert r.status_code == 422, r.text


async def test_proposed_actions_scoped_to_org(
    client: AsyncClient,
    org_a,
    org_b,
    analyst_a_token,
    analyst_b_token,
    runner_secret,
    admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)
    action_id = await _propose_case_patch(client, token, case_id, "Org A only")

    # Org B analyst sees none of org A's proposals and cannot approve one.
    listed = await client.get(_ACTIONS, headers=_user_h(analyst_b_token, org_b.id))
    assert listed.status_code == 200, listed.text
    assert all(a["id"] != action_id for a in listed.json())
    denied = await client.post(
        f"{_ACTIONS}/{action_id}/approve",
        headers=_user_h(analyst_b_token, org_b.id),
    )
    assert denied.status_code == 404, denied.text
