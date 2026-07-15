from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import task as task_crud
from app.models.case_ import Case, CaseCreate
from app.models.case_share import CaseShare
from app.models.task import Task, TaskCreate
from app.models.organisation_link import (
    AutoShareMode,
    CaseSharingMode,
    OrganisationLink,
)


async def test_create_and_get_case(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    response = await client.post(
        "/api/v1/cases/",
        json={"title": "phishing report", "severity": 3},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["title"] == "phishing report"
    assert case["severity"] == 3
    assert isinstance(case["id"], int)

    got = await client.get(
        f"/api/v1/cases/{case['id']}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert got.status_code == 200
    assert got.json()["title"] == "phishing report"


def test_compute_case_sla_states():
    from datetime import datetime, timedelta

    from app.crud.sla import compute_case_sla

    created = datetime(2026, 7, 12, 10, 0, 0)
    now = created + timedelta(hours=2)

    # No policy for the severity → both None.
    assert compute_case_sla(
        severity=1, created_at=created, is_open=True, resolve_targets={2: 3600}, now=now
    ) == (None, None)

    # Past the 1h target after 2h → breached; due_at is created+target.
    due, state = compute_case_sla(
        severity=2, created_at=created, is_open=True, resolve_targets={2: 3600}, now=now
    )
    assert state == "breached" and due == created + timedelta(hours=1)

    # 4h target, 2h elapsed (< 80% = 3.2h) → ok.
    _, state = compute_case_sla(
        severity=2, created_at=created, is_open=True, resolve_targets={2: 14400}, now=now
    )
    assert state == "ok"

    # 2.5h target → 80% = 2h elapsed exactly → at-risk.
    _, state = compute_case_sla(
        severity=2, created_at=created, is_open=True, resolve_targets={2: 9000}, now=now
    )
    assert state == "at-risk"

    # A non-open case has no live countdown → state None but due_at still set.
    due, state = compute_case_sla(
        severity=2, created_at=created, is_open=False, resolve_targets={2: 3600}, now=now
    )
    assert state is None and due == created + timedelta(hours=1)


async def test_case_exposes_sla_due_and_state(
    client: AsyncClient, org_a, builtin_roles, analyst_a, analyst_a_token
):
    h = {"Authorization": f"Bearer {analyst_a_token}", "X-Organisation-Id": org_a.id}
    created = await client.post(
        "/api/v1/cases/", json={"title": "sla case", "severity": 3}, headers=h
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    # No policy yet → SLA fields are null.
    got = await client.get(f"/api/v1/cases/{case_id}", headers=h)
    assert got.json()["sla_due_at"] is None
    assert got.json()["sla_state"] is None

    # Add a generous resolve target for severity 3; a just-created open case is "ok".
    put = await client.put(
        "/api/v1/sla-policies/",
        json=[{"severity": 3, "ack_seconds": 3600, "resolve_seconds": 86400}],
        headers=h,
    )
    assert put.status_code == 200, put.text

    got = await client.get(f"/api/v1/cases/{case_id}", headers=h)
    body = got.json()
    assert body["sla_due_at"] is not None
    assert body["sla_state"] == "ok"

    # The list projection carries the same fields.
    listed = await client.get("/api/v1/cases/", headers=h)
    row = next(c for c in listed.json()["items"] if c["id"] == case_id)
    assert row["sla_state"] == "ok"
    assert row["sla_due_at"] is not None


async def test_list_cases_only_returns_shared(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_b_token,
):
    # Create cases owned by org-a
    await case_crud.create_case(
        session,
        CaseCreate(title="a-only"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await case_crud.create_case(
        session,
        CaseCreate(title="b-only"),
        owner_org_id=org_b.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_b.id),
    )
    response = await client.get(
        "/api/v1/cases/",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 200
    body = response.json()
    titles = [c["title"] for c in body["items"]]
    assert titles == ["b-only"]
    assert body["total"] == 1


async def test_get_case_not_shared_returns_404(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="secret"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    response = await client.get(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 404


async def test_create_case_requires_write_case(
    client: AsyncClient, org_a, builtin_roles, readonly_a, readonly_a_token
):
    response = await client.post(
        "/api/v1/cases/",
        json={"title": "no"},
        headers={
            "Authorization": f"Bearer {readonly_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 403


async def test_missing_org_header_400(client: AsyncClient, analyst_a_token):
    response = await client.get(
        "/api/v1/cases/",
        headers={"Authorization": f"Bearer {analyst_a_token}"},
    )
    assert response.status_code == 400


async def test_delete_case_owner_only(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="del"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    # Share with org-b as analyst (write:case included via builtin)
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()

    # b cannot delete
    response = await client.delete(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403

    # a can delete
    response = await client.delete(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 204


def test_delete_grants_are_split_from_write_groups():
    """Grant separation (finale): write:<domain> no longer expands to any delete:*
    capability — those live in the standalone delete:<domain> group."""
    from app.models.role import expand_permissions

    # write groups grant edit but NOT delete.
    inv_write = expand_permissions({"write:investigation"})
    assert {"write:case", "write:task", "write:observable", "write:alert"} <= inv_write
    assert not any(c.startswith("delete:") for c in inv_write)

    for grp in ("write:intel", "write:org", "write:access"):
        assert not any(c.startswith("delete:") for c in expand_permissions({grp}))

    # The delete groups grant exactly the delete capabilities for their domain.
    assert expand_permissions({"delete:investigation"}) == {
        "delete:case", "delete:task", "delete:observable", "delete:alert",
    }
    assert expand_permissions({"delete:intel"}) == {
        "delete:custom_field", "delete:knowledge_base",
    }
    assert expand_permissions({"delete:access"}) == {"delete:user", "delete:role"}
    assert expand_permissions({"delete:org"}) == {"delete:organisation"}

    # A raw write:case capability alone still does NOT imply delete:case.
    assert "delete:case" not in expand_permissions({"write:case"})


def test_builtin_roles_delete_grants():
    """org-admin holds every delete group; analyst keeps delete on investigation
    (it had it via write:investigation before the split); read-only has none."""
    from app.models.role import BUILTIN_ROLES, Permission, expand_permissions

    admin = {p.value for p in BUILTIN_ROLES["org-admin"]}
    assert {
        "delete:investigation", "delete:intel", "delete:org", "delete:access",
    } <= admin

    analyst = {p.value for p in BUILTIN_ROLES["analyst"]}
    assert "delete:investigation" in analyst
    assert "delete:intel" not in analyst  # analyst only reads intel
    assert "delete:case" in expand_permissions(analyst)

    readonly = expand_permissions(p.value for p in BUILTIN_ROLES["read-only"])
    assert not any(c.startswith("delete:") for c in readonly)
    assert Permission.delete_investigation not in BUILTIN_ROLES["read-only"]


async def test_delete_case_forbidden_for_readonly_owner_member(
    client: AsyncClient,
    session,
    org_a,
    builtin_roles,
    analyst_a,
    readonly_a,
    readonly_a_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="ro-del"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await session.commit()
    # A read-only member of the owner org lacks delete:case → 403 (delete is no
    # longer implied by mere read/visibility).
    response = await client.delete(
        f"/api/v1/cases/{case.id}",
        headers={
            "Authorization": f"Bearer {readonly_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 403


async def test_delete_case_soft_deletes_and_cascades(
    client: AsyncClient,
    session,
    engine,
    org_a,
    builtin_roles,
    analyst_a,
    analyst_a_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="soft"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    task = await task_crud.create_task(
        session,
        TaskCreate(title="t1"),
        case_id=case.id,
        organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    await session.commit()

    headers = {
        "Authorization": f"Bearer {analyst_a_token}",
        "X-Organisation-Id": org_a.id,
    }
    response = await client.delete(f"/api/v1/cases/{case.id}", headers=headers)
    assert response.status_code == 204

    # Read paths treat it as gone...
    assert (await client.get(f"/api/v1/cases/{case.id}", headers=headers)).status_code == 404
    listed = await client.get("/api/v1/cases/", headers=headers)
    assert all(c["id"] != case.id for c in listed.json()["items"])

    # ...but the rows survive, flagged, and the child task is cascaded. Use a
    # fresh session so we read committed DB state, not the request's identity map.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        db_case = await verify.get(Case, case.id)
        assert db_case is not None and db_case.deleted_at is not None
        db_task = await verify.get(Task, (task.case_id, task.id))
        assert db_task is not None and db_task.deleted_at is not None


async def test_change_tlp_owner_only(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="tlp"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()

    response = await client.patch(
        f"/api/v1/cases/{case.id}",
        json={"tlp": 3},
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 403


async def test_multiorg_autoshare_task_visible(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    analyst_a,
    analyst_b,
    analyst_a_token,
    analyst_b_token,
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="multiorg"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    session.add(
        CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            role_id=builtin_roles["analyst"].id,
            is_owner=False,
            created_by=str(analyst_a.id),
        )
    )
    session.add(
        OrganisationLink(
            from_org_id=org_a.id,
            to_org_id=org_b.id,
            case_sharing=CaseSharingMode.manual,
            task_sharing=AutoShareMode.auto_share,
            observable_sharing=AutoShareMode.manual,
            created_by=str(analyst_a.id),
        )
    )
    await session.commit()

    # Create task as org-a
    response = await client.post(
        f"/api/v1/cases/{case.id}/tasks",
        json={"title": "investigate"},
        headers={
            "Authorization": f"Bearer {analyst_a_token}",
            "X-Organisation-Id": org_a.id,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["public_id"] == f"T-{case.id}-1"

    # org-b should see the task in the case's task list
    response = await client.get(
        f"/api/v1/cases/{case.id}/tasks",
        headers={
            "Authorization": f"Bearer {analyst_b_token}",
            "X-Organisation-Id": org_b.id,
        },
    )
    assert response.status_code == 200
    items = response.json()["items"]
    titles = [t["title"] for t in items]
    assert titles == ["investigate"]
    assert items[0]["public_id"] == f"T-{case.id}-1"


async def test_case_counts_endpoint(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="counts"),
        owner_org_id=org_a.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(analyst_a.id),
    )
    await session.commit()
    h = {
        "Authorization": f"Bearer {analyst_a_token}",
        "X-Organisation-Id": org_a.id,
    }

    # A fresh case has zero of everything.
    empty = await client.get(f"/api/v1/cases/{case.id}/counts", headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "tasks": 0,
        "custom_fields": 0,
        "comments": 0,
        "attachments": 0,
        "observables": 0,
        "similar": 0,
    }

    # Add one task, two comments, and one observable.
    assert (
        await client.post(
            f"/api/v1/cases/{case.id}/tasks", json={"title": "t1"}, headers=h
        )
    ).status_code == 201
    for msg in ("c1", "c2"):
        assert (
            await client.post(
                f"/api/v1/cases/{case.id}/comments", json={"message": msg}, headers=h
            )
        ).status_code == 201
    assert (
        await client.post(
            f"/api/v1/cases/{case.id}/observables",
            json={"observable_type": "ip", "data": "1.2.3.4"},
            headers=h,
        )
    ).status_code == 201

    counts = await client.get(f"/api/v1/cases/{case.id}/counts", headers=h)
    assert counts.status_code == 200, counts.text
    assert counts.json() == {
        "tasks": 1,
        "custom_fields": 0,
        "comments": 2,
        "attachments": 0,
        "observables": 1,
        "similar": 0,
    }
