"""Public proposed-action review: plugins propose canonical edits, analysts
approve/reject, approval applies through normal CRUD with a combined actor.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenPayload, create_access_token
from app.crud import plugin_proposed_action as ppa_crud
from app.crud.organisation_member import add_member
from app.crud.user import create_user
from app.models.organisation_member import OrganisationMemberCreate
from app.models.plugin_runner import PluginRun
from app.models.user import UserCreate

from tests.test_api_plugin_runners import RUNNER1, _enable_plugin_for_org, _register_runner
from tests.test_api_plugin_runtime import (
    RUNTIME_MANIFEST,
    _create_case_with_observable,
    _runner_h,
    _runtime_h,
    _runtime_token_for,
)

_RUNTIME_PREFIX = "/api/internal/plugin-runtime"
_RUNNER_PREFIX = "/api/internal/plugin-runner"
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


async def _runtime_run_id(client, runner_secret, admin_token, org_id, case_id) -> str:
    """Register a runner + plugin and start a run scoped to `case_id`, returning
    the `PluginRun.id`. `add_related_observable`/`execute_responder_action` have
    no runtime HTTP endpoint yet, so tests build the PluginProposedAction row
    directly via `ppa_crud.create`, which needs a real `PluginRun` to attribute
    the proposal to."""
    manifest = RUNTIME_MANIFEST
    _, credential = await _register_runner(client, admin_token, RUNNER1, plugins=[manifest])
    h = _runner_h(credential)
    await _enable_plugin_for_org(client, admin_token, org_id, manifest["id"])
    r = await client.post(
        f"{_RUNNER_PREFIX}/runs",
        json={
            "event_id": f"audit:{uuid.uuid4()}",
            "event_type": "case.created",
            "organisation_id": org_id,
            "plugin_id": manifest["id"],
            "plugin_version": manifest["version"],
            "runner_id": RUNNER1["id"],
            "event_object": {"type": "case", "id": str(case_id)},
            "trigger_metadata": {},
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    await client.post(f"{_RUNNER_PREFIX}/runs/{run_id}/accepted", headers=h)
    return run_id


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


async def test_add_related_observable_propose_then_approve_applies(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    action = await ppa_crud.create(
        session,
        run=run,
        action_type="add_related_observable",
        entity_type="case",
        entity_id=str(case_id),
        payload={"observable_type": "ip", "data": "9.9.9.9", "ioc": True},
    )
    assert action.status == "proposed"

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.status_code == 200, obs.text
    items = obs.json()["items"]
    linked = next((o for o in items if o["data"] == "9.9.9.9"), None)
    assert linked is not None
    assert linked["observable_type"] == "ip"
    assert linked["ioc"] is True

    activity = await client.get(f"/api/v1/cases/{case_id}/activity?limit=50", headers=h)
    assert activity.status_code == 200, activity.text
    audit_row = next(
        (a for a in activity.json()["items"] if a["object_id"] == linked["id"]), None
    )
    assert audit_row is not None
    assert audit_row["actor"].startswith(f"plugin:{action.plugin_id}@")
    assert audit_row["actor"].endswith(f"approved-by user:{analyst_a.id}")


async def test_add_related_observable_already_on_case_is_idempotent(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """Proposing an observable that's already linked to the case applies as a
    no-op (link semantics) rather than failing with a conflict."""
    case_id, _obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    # _create_case_with_observable already created type=ip data=1.2.3.4 on this case.
    action = await ppa_crud.create(
        session,
        run=run,
        action_type="add_related_observable",
        entity_type="case",
        entity_id=str(case_id),
        payload={"observable_type": "ip", "data": "1.2.3.4"},
    )

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.status_code == 200, obs.text
    matches = [o for o in obs.json()["items"] if o["data"] == "1.2.3.4"]
    assert len(matches) == 1


async def test_execute_responder_action_rejected_on_apply(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """execute_responder_action has no post-approval execution path. Approval
    must fail explicitly (status=failed with a clear reason) rather than
    crash or silently fall through to the add_tag branch."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    action = await ppa_crud.create(
        session,
        run=run,
        action_type="execute_responder_action",
        entity_type="case",
        entity_id=str(case_id),
        payload={"responder": "block-ip", "tag": "should-not-be-used-as-a-tag"},
    )
    assert action.status == "proposed"

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "failed"
    assert "execute_responder_action" in body["decision_reason"]

    # No tag was added to the case (proof it didn't fall through to add_tag).
    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert tags.status_code == 200, tags.text
    assert "should-not-be-used-as-a-tag" not in tags.json()


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
