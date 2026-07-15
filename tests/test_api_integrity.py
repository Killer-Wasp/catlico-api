"""§6.6: the report-only GET /admin/integrity superadmin route and its three checks."""
import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlmodel import select

from app.crud import case_ as case_crud
from app.models.case_ import Case, CaseCreate
from app.models.plugin_runner import PluginDefinition, PluginRunDaily
from app.models.task import Task
from app.services.integrity import check_seq_high_water


def _headers(token, org_id=None):
    h = {"Authorization": f"Bearer {token}"}
    if org_id:
        h["X-Organisation-Id"] = org_id
    return h


async def test_integrity_requires_superadmin(client: AsyncClient, viewer_token):
    r = await client.get("/api/v1/admin/integrity", headers=_headers(viewer_token))
    assert r.status_code == 403


async def test_integrity_report_shape_clean(client: AsyncClient, admin_token):
    r = await client.get("/api/v1/admin/integrity", headers=_headers(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"checked_at", "rollup_drift", "seq_high_water", "orphan_blobs"}
    assert body["rollup_drift"]["mismatches"] == 0
    assert body["seq_high_water"]["violations"] == 0
    assert body["orphan_blobs"]["orphans"] == 0


async def test_integrity_detects_rollup_drift(
    client: AsyncClient, session, org_a, admin_token
):
    """A PluginRunDaily row with counts but no surviving runs (within retention) is a
    recompute mismatch."""
    pdef = PluginDefinition(id=f"plugin-{uuid.uuid4().hex[:8]}", display_name="P")
    session.add(pdef)
    await session.flush()
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    session.add(
        PluginRunDaily(
            organisation_id=org_a.id,
            plugin_id=pdef.id,
            day=day,
            success_count=5,
        )
    )
    await session.flush()

    r = await client.get("/api/v1/admin/integrity", headers=_headers(admin_token))
    assert r.status_code == 200, r.text
    drift = r.json()["rollup_drift"]
    assert drift["mismatches"] >= 1
    assert any(d["plugin_id"] == pdef.id for d in drift["details"])


async def test_seq_high_water_flags_violation(
    session, org_a, builtin_roles, admin_user
):
    """§6.6 check (2): a counter at/below its highest handed-out child id is reported."""
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(admin_user.id),
    )
    # Force the invariant break: a task id above the counter's next value.
    session.add(
        Task(case_id=case.id, id=10, organisation_id=org_a.id, title="t", created_by=str(admin_user.id))
    )
    stored = await session.get(Case, case.id)
    stored.next_task_seq = 1
    await session.flush()

    report = await check_seq_high_water(session)
    assert report["violations"] >= 1
    assert any(
        v["scope"] == "case.next_task_seq" and v["case_id"] == case.id
        for v in report["details"]
    )


async def test_seq_high_water_clean(session, org_a, builtin_roles, admin_user):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(admin_user.id),
    )
    session.add(Task(case_id=case.id, id=1, organisation_id=org_a.id, title="t", created_by=str(admin_user.id)))
    stored = await session.get(Case, case.id)
    stored.next_task_seq = 2  # next > max(id)=1
    await session.flush()

    report = await check_seq_high_water(session)
    assert not any(
        v.get("case_id") == case.id for v in report["details"]
    )
