"""Public proposed-action review: plugins propose canonical edits, analysts
approve/reject, approval applies through normal CRUD with a combined actor.
"""
import uuid

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenPayload, create_access_token
from app.crud import observable as obs_crud
from app.crud import plugin_proposed_action as ppa_crud
from app.crud.organisation_member import add_member
from app.crud.user import create_user
from app.models.organisation_member import OrganisationMemberCreate
from app.models.plugin_runner import PluginRun
from app.models.audit import Audit
from sqlalchemy import select
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
    the `PluginRun.id`. `add_related_observable` has no runtime HTTP endpoint yet,
    so tests build the PluginProposedAction row directly via `ppa_crud.create`,
    which needs a real `PluginRun` to attribute the proposal to."""
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


async def _enable_auto_apply(session, org_id, plugin_id, actions):
    """Opt the org's plugin into auto-applying `actions`. `_runtime_run_id`
    has already created the OrgPlugin (enabling the plugin); we just flip the
    policy. `ppa_crud.create` reads this row to decide whether to auto-apply."""
    from app.models.plugin_runner import OrgPlugin

    org_plugin = await session.get(OrgPlugin, (org_id, plugin_id))
    assert org_plugin is not None, "plugin must be enabled before setting a policy"
    org_plugin.auto_apply_actions = list(actions)
    await session.flush()


# The generic, non-leaking reason auto-apply/decide store on an IntegrityError.
_GENERIC_INTEGRITY_REASON = (
    "Could not apply: the change conflicted with existing data "
    "(database integrity constraint)."
)


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

    # The /activity viewer endpoint was removed; assert the audit row directly.
    rows = (
        await session.execute(select(Audit).where(Audit.object_id == linked["id"]))
    ).scalars().all()
    audit_row = next((a for a in rows if a.actor and "approved-by" in a.actor), None)
    assert audit_row is not None
    assert audit_row.actor.startswith(f"plugin:{action.plugin_id}@")
    assert audit_row.actor.endswith(f"approved-by user:{analyst_a.id}")


async def _propose_observable_patch(client, token, obs_id, body):
    r = await client.patch(
        f"{_RUNTIME_PREFIX}/observables/{obs_id}",
        json=body,
        headers=_runtime_h(token),
    )
    assert r.status_code == 202, r.text
    return r.json()["proposed_action_id"]


async def test_patch_observable_propose_then_approve_applies(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id),
    )
    action_id = await _propose_observable_patch(
        client, token, obs_id, {"ioc": True, "sighted": True, "message": "malicious"}
    )

    h = _user_h(analyst_a_token, org_a.id)
    listed = await client.get(
        f"{_ACTIONS}?entity_type=observable&entity_id={obs_id}", headers=h
    )
    assert listed.status_code == 200, listed.text
    assert any(a["id"] == action_id and a["status"] == "proposed" for a in listed.json())

    approved = await client.post(f"{_ACTIONS}/{action_id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    linked = next(o for o in obs.json()["items"] if o["id"] == str(obs_id))
    assert linked["ioc"] is True
    assert linked["sighted"] is True
    assert linked["message"] == "malicious"

    # Applied under the combined plugin + approving-analyst actor. The /activity
    # viewer endpoint was removed; assert the audit row directly.
    rows = (
        await session.execute(select(Audit).where(Audit.object_id == str(obs_id)))
    ).scalars().all()
    audit_row = next((a for a in rows if a.actor and "approved-by" in a.actor), None)
    assert audit_row is not None
    assert audit_row.actor.endswith(f"approved-by user:{analyst_a.id}")


async def test_patch_observable_requires_write_observable_to_approve(
    client: AsyncClient, org_a, analyst_a_token, readonly_a_token, runner_secret, admin_token,
):
    _, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _runtime_token_for(
        client, runner_secret, admin_token, org_a.id, event_object_id=str(obs_id),
    )
    action_id = await _propose_observable_patch(client, token, obs_id, {"ioc": True})

    # A read-only member (no write:observable) cannot approve the patch.
    denied = await client.post(
        f"{_ACTIONS}/{action_id}/approve", headers=_user_h(readonly_a_token, org_a.id)
    )
    assert denied.status_code == 403, denied.text


async def test_patch_observable_never_auto_applies_even_when_opted_in(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """patch_observable is not in LOW_RISK_ACTIONS, so it is analyst-gated even if
    the org (mistakenly) lists it in auto_apply_actions — it stays 'proposed'."""
    case_id, obs_id = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    await _enable_auto_apply(session, org_a.id, RUNTIME_MANIFEST["id"], ["patch_observable"])
    run = await session.get(PluginRun, uuid.UUID(run_id))

    action = await ppa_crud.create(
        session,
        run=run,
        action_type="patch_observable",
        entity_type="observable",
        entity_id=str(obs_id),
        payload={"ioc": True},
    )
    assert action.status == "proposed"  # not auto-applied


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


async def test_add_related_observable_dedup_race_resolves_as_applied_noop(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """The check-then-insert in the add_related_observable branch is a TOCTOU: two
    approvals of the same observable both see find_case_observable -> None and both
    call create_case_observable; the partial unique index rejects the loser with an
    IntegrityError. Simulate it deterministically by making the pre-check report the
    row absent while it in fact exists. The loser must resolve as an applied no-op
    (exactly one row, same as the non-racing short-circuit) -- never a 500."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    # The case already carries ip/1.2.3.4 (from _create_case_with_observable), so
    # the insert below will collide on uq_observable_case_dedup for real.
    action = await ppa_crud.create(
        session,
        run=run,
        action_type="add_related_observable",
        entity_type="case",
        entity_id=str(case_id),
        payload={"observable_type": "ip", "data": "1.2.3.4"},
    )

    # First find (the pre-check) sees nothing, as if the concurrent approver's
    # insert hadn't landed yet; later finds (the post-IntegrityError re-check)
    # see the truth.
    real_find = obs_crud.find_case_observable
    calls = {"n": 0}

    async def racy_find(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(obs_crud, "find_case_observable", racy_find)

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "applied"

    monkeypatch.undo()
    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.status_code == 200, obs.text
    matches = [o for o in obs.json()["items"] if o["data"] == "1.2.3.4"]
    assert len(matches) == 1


async def test_non_dedup_integrity_error_ends_failed_not_500(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """A genuine (non-dedup) IntegrityError out of apply() must land the action at
    status='failed' with a reason -- never an unhandled 500, never a stuck
    'proposed'.

    This is also the savepoint regression guard: apply()'s failing statement aborts
    the transaction, so without decide()'s begin_nested savepoint the later
    status='failed' flush would itself fail on the poisoned transaction and 500.
    Removing the savepoint makes this test fail with 500."""
    from sqlalchemy import text

    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    action = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"},
    )

    async def boom_apply(session, action, *, approver_user_id):
        # A real NOT NULL violation: this INSERT omits several non-nullable
        # observable columns, and observable.message (NOT NULL, no default at the
        # DB level) is the first to fire -> NotNullViolationError. Any genuine
        # NOT NULL violation aborts the transaction, which is the point here: it
        # stands in for a real non-dedup IntegrityError bubbling out of a CRUD
        # call.
        await session.execute(
            text(
                "INSERT INTO observable (id, observable_type, data) "
                "VALUES (gen_random_uuid(), 'ip', 'boom')"
            )
        )

    monkeypatch.setattr(ppa_crud, "apply", boom_apply)

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "failed"
    assert body["decision_reason"], "a genuine failure must carry an informative reason"


async def test_add_related_observable_non_dedup_integrity_not_swallowed(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """The dedup-race handler must not blanket-treat every IntegrityError as a
    benign no-op. If the observable still doesn't exist on re-check, the error was
    some other constraint and must surface as failed, not applied."""
    from sqlalchemy.exc import IntegrityError

    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    action = await ppa_crud.create(
        session, run=run, action_type="add_related_observable", entity_type="case",
        entity_id=str(case_id), payload={"observable_type": "ip", "data": "203.0.113.7"},
    )

    async def boom_create(*args, **kwargs):
        raise IntegrityError("INSERT ...", {}, Exception("some other constraint"))

    monkeypatch.setattr(obs_crud, "create_case_observable", boom_create)

    h = _user_h(analyst_a_token, org_a.id)
    approved = await client.post(f"{_ACTIONS}/{action.id}/approve", headers=h)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "failed"
    assert body["decision_reason"]

    monkeypatch.undo()
    # The re-check found nothing, so it was not a benign no-op: nothing created.
    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.status_code == 200, obs.text
    assert not [o for o in obs.json()["items"] if o["data"] == "203.0.113.7"]


async def test_auto_apply_non_dedup_integrity_error_ends_failed_not_500(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """The org-policy auto-apply path in create() must survive a genuine
    (non-dedup) IntegrityError out of apply(): the row lands at status='failed',
    never an unhandled 500 / InFailedSQLTransactionError, never stuck 'proposed'.

    Savepoint regression guard: boom_apply's INSERT aborts the transaction, so
    without create()'s begin_nested savepoint the terminal status='failed' flush
    would itself fail on the poisoned transaction. Removing the savepoint makes
    this test error with InFailedSQLTransactionError."""
    from sqlalchemy import text

    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    await _enable_auto_apply(session, org_a.id, run.plugin_id, ["add_tag"])

    async def boom_apply(session, action, *, approver_user_id):
        # A real NOT NULL violation (observable.message is NOT NULL with no DB
        # default): genuinely aborts the transaction, standing in for a non-dedup
        # IntegrityError bubbling out of a CRUD call during auto-apply.
        await session.execute(
            text(
                "INSERT INTO observable (id, observable_type, data) "
                "VALUES (gen_random_uuid(), 'ip', 'boom')"
            )
        )

    monkeypatch.setattr(ppa_crud, "apply", boom_apply)

    # No exception escapes create(): the auto-apply record reaches a terminal
    # status instead of poisoning the request.
    action = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"},
    )
    assert action.status == "failed"
    assert action.decided_by == "system:auto-apply"
    assert action.decision_reason, "a genuine failure must carry an informative reason"


async def test_auto_apply_integrity_error_stores_generic_reason_not_driver_text(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """decision_reason is exposed by public(): on an auto-apply IntegrityError the
    stored reason must be the generic message, never the raw driver detail
    (column/constraint names, SQL)."""
    from sqlalchemy import text

    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    await _enable_auto_apply(session, org_a.id, run.plugin_id, ["add_tag"])

    async def boom_apply(session, action, *, approver_user_id):
        await session.execute(
            text(
                "INSERT INTO observable (id, observable_type, data) "
                "VALUES (gen_random_uuid(), 'ip', 'boom')"
            )
        )

    monkeypatch.setattr(ppa_crud, "apply", boom_apply)

    action = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"},
    )
    assert action.status == "failed"
    assert action.decision_reason == _GENERIC_INTEGRITY_REASON
    # None of the raw driver internals leak into the persisted/public reason.
    leaked = ("null value", "NotNullViolation", "column", "INSERT", "observable")
    lowered = action.decision_reason.lower()
    assert not any(bit.lower() in lowered for bit in leaked), action.decision_reason


async def test_auto_apply_add_related_observable_dedup_race_resolves_as_applied_noop(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """The dedup TOCTOU race during auto-apply (add_related_observable) must
    resolve as a benign no-op: born 'applied', exactly one observable, never a
    500. The add_related_observable branch's own inner savepoint absorbs the
    collision; create()'s outer savepoint must not turn it into a failure."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    await _enable_auto_apply(session, org_a.id, run.plugin_id, ["add_related_observable"])

    # First find (pre-check) reports the row absent as if a concurrent approver's
    # insert hadn't landed; the post-IntegrityError re-check sees the truth.
    real_find = obs_crud.find_case_observable
    calls = {"n": 0}

    async def racy_find(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(obs_crud, "find_case_observable", racy_find)

    action = await ppa_crud.create(
        session, run=run, action_type="add_related_observable", entity_type="case",
        entity_id=str(case_id), payload={"observable_type": "ip", "data": "1.2.3.4"},
    )
    assert action.status == "applied"
    assert action.decided_by == "system:auto-apply"

    monkeypatch.undo()
    h = _user_h(analyst_a_token, org_a.id)
    obs = await client.get(f"/api/v1/cases/{case_id}/observables", headers=h)
    assert obs.status_code == 200, obs.text
    matches = [o for o in obs.json()["items"] if o["data"] == "1.2.3.4"]
    assert len(matches) == 1


async def test_execute_responder_action_is_not_a_known_action(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """`execute_responder_action` was dropped: it had no producer and no executor
    (dead enum member). Proposing it is now an unknown action_type — a 422 at
    create time — rather than a proposal that fails on approval. (Re-add if a
    plugin-native responder proposal-chaining path is ever designed.)"""
    from app.crud import plugin_proposed_action as ppa

    assert "execute_responder_action" not in ppa.ACTION_TYPES

    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    with pytest.raises(HTTPException) as exc_info:
        await ppa_crud.create(
            session,
            run=run,
            action_type="execute_responder_action",
            entity_type="case",
            entity_id=str(case_id),
            payload={"responder": "block-ip"},
        )
    assert exc_info.value.status_code == 422
    assert "execute_responder_action" in str(exc_info.value.detail)


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


# --- Fingerprint idempotency: dedup within a run ---


async def _case_action_rows(client, analyst_a_token, org_a, case_id, action_type):
    h = _user_h(analyst_a_token, org_a.id)
    listed = await client.get(
        f"{_ACTIONS}?entity_type=case&entity_id={case_id}", headers=h
    )
    assert listed.status_code == 200, listed.text
    return [a for a in listed.json() if a["action_type"] == action_type]


async def test_patch_case_propose_twice_dedups_within_run(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    """Re-proposing the same patch_case from the same run (redelivery/retry)
    returns the existing proposal — exactly one row, no 500, no duplicate."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)

    first = await client.patch(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        json={"description": "Same patch"},
        headers=_runtime_h(token),
    )
    second = await client.patch(
        f"{_RUNTIME_PREFIX}/cases/{case_id}",
        json={"description": "Same patch"},
        headers=_runtime_h(token),
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["proposed_action_id"] == second.json()["proposed_action_id"]

    rows = await _case_action_rows(
        client, analyst_a_token, org_a, case_id, "patch_case_description"
    )
    assert len(rows) == 1


async def test_add_tag_propose_twice_dedups_within_run(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)

    first = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "dupe"},
        headers=_runtime_h(token),
    )
    second = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "dupe"},
        headers=_runtime_h(token),
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["proposed_action_id"] == second.json()["proposed_action_id"]

    rows = await _case_action_rows(client, analyst_a_token, org_a, case_id, "add_tag")
    assert len(rows) == 1


async def test_create_task_propose_twice_dedups_within_run(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)

    body = {"title": "Investigate", "description": "look"}
    first = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tasks", json=body, headers=_runtime_h(token)
    )
    second = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tasks", json=body, headers=_runtime_h(token)
    )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["proposed_action_id"] == second.json()["proposed_action_id"]

    rows = await _case_action_rows(
        client, analyst_a_token, org_a, case_id, "create_task"
    )
    assert len(rows) == 1


async def test_different_payload_creates_distinct_proposal(
    client: AsyncClient, org_a, analyst_a_token, runner_secret, admin_token,
):
    """A different payload is a different fingerprint: it must NOT dedup."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    token = await _case_runtime_token(client, runner_secret, admin_token, org_a, case_id)

    first = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "alpha"},
        headers=_runtime_h(token),
    )
    second = await client.post(
        f"{_RUNTIME_PREFIX}/cases/{case_id}/tags",
        json={"tag": "beta"},
        headers=_runtime_h(token),
    )
    assert first.json()["proposed_action_id"] != second.json()["proposed_action_id"]

    rows = await _case_action_rows(client, analyst_a_token, org_a, case_id, "add_tag")
    assert len(rows) == 2


async def test_same_content_different_run_is_not_deduped(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """Dedup is scoped to (plugin_run_id, fingerprint): a genuinely different run
    (a different event) may propose identical content and get its own row."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id_1 = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run_id_2 = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    assert run_id_1 != run_id_2
    run1 = await session.get(PluginRun, uuid.UUID(run_id_1))
    run2 = await session.get(PluginRun, uuid.UUID(run_id_2))

    fp = "shared-fingerprint"
    first = await ppa_crud.create(
        session, run=run1, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "shared"}, fingerprint=fp,
    )
    second = await ppa_crud.create(
        session, run=run2, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "shared"}, fingerprint=fp,
    )
    assert first.id != second.id

    rows = await _case_action_rows(client, analyst_a_token, org_a, case_id, "add_tag")
    assert len(rows) == 2


async def test_dedup_holds_across_run_retry_reusing_run_row(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """A retried run reuses the same PluginRun row (bumping attempt). Re-proposing
    the same fingerprint on that reused run returns the existing row, not a dup."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    fp = "retry-fingerprint"
    first = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"}, fingerprint=fp,
    )
    second = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"}, fingerprint=fp,
    )
    assert first.id == second.id


async def test_create_concurrent_duplicate_resolves_to_existing_row(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token, monkeypatch,
):
    """The fingerprint pre-check is a TOCTOU: a concurrent duplicate can insert
    the row between our pre-check and our flush, tripping the partial unique
    index. Simulate it deterministically by making the pre-check report the row
    absent while it in fact exists (mirrors the add_related_observable racy_find
    tests). The loser must roll back to the savepoint, re-select, and return the
    existing row — never a 500, never a duplicate."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))

    fp = "race-fingerprint"
    first = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"}, fingerprint=fp,
    )

    # First lookup (the pre-check) sees nothing, as if the concurrent insert
    # hadn't landed yet; the post-IntegrityError re-select sees the truth.
    real = ppa_crud._existing_by_fingerprint
    calls = {"n": 0}

    async def racy(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real(*args, **kwargs)

    monkeypatch.setattr(ppa_crud, "_existing_by_fingerprint", racy)

    second = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "x"}, fingerprint=fp,
    )
    # Resolved to the pre-existing row via the IntegrityError re-select branch.
    assert second.id == first.id
    assert calls["n"] == 2

    monkeypatch.undo()
    rows = await _case_action_rows(client, analyst_a_token, org_a, case_id, "add_tag")
    assert len(rows) == 1


async def test_dedup_does_not_reapply_autoapplied_action(
    client: AsyncClient, session: AsyncSession, org_a, analyst_a_token, runner_secret, admin_token,
):
    """When an auto-applied low-risk action is re-proposed with the same
    fingerprint, the existing (already 'applied') row is returned WITHOUT running
    the auto-apply policy again — the change is applied exactly once."""
    case_id, _ = await _create_case_with_observable(client, org_a, analyst_a_token)
    run_id = await _runtime_run_id(client, runner_secret, admin_token, org_a.id, case_id)
    run = await session.get(PluginRun, uuid.UUID(run_id))
    await _enable_auto_apply(session, org_a.id, run.plugin_id, ["add_tag"])

    fp = "autoapply-fingerprint"
    first = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "auto"}, fingerprint=fp,
    )
    assert first.status == "applied"
    second = await ppa_crud.create(
        session, run=run, action_type="add_tag", entity_type="case",
        entity_id=str(case_id), payload={"tag": "auto"}, fingerprint=fp,
    )
    assert second.id == first.id
    assert second.status == "applied"

    # The tag was applied exactly once (no duplicate tag from a re-apply).
    h = _user_h(analyst_a_token, org_a.id)
    tags = await client.get(f"/api/v1/cases/{case_id}/tags", headers=h)
    assert tags.status_code == 200, tags.text
    assert tags.json().count("auto") == 1


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
