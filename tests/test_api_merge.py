from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.crud import case_ as case_crud
from app.crud import observable as obs_crud
from app.crud import tag as tag_crud
from app.crud import task as task_crud
from app.models.audit import Audit
from app.models.case_ import CaseCreate
from app.models.case_merge import CaseMerge
from app.models.observable import Observable, ObservableCreate
from app.models.tag import Tagging, TaggableType
from app.models.task import TaskCreate


async def _make_case(session, org, builtin_roles, actor, title, **kw):
    case = await case_crud.create_case(
        session,
        CaseCreate(title=title, **kw),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(actor.id),
    )
    return case


def _hdr(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def test_merge_happy_path_reparents_and_freezes(
    client: AsyncClient, session, engine, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "phish-1", severity=2)
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "phish-2", severity=3)
    await task_crud.create_task(
        session, TaskCreate(title="t1"), case_id=c1.id,
        organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await task_crud.create_task(
        session, TaskCreate(title="t2"), case_id=c2.id,
        organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await obs_crud.create_case_observable(
        session, ObservableCreate(observable_type="ip", data="1.1.1.1"),
        case_id=c1.id, organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await obs_crud.create_case_observable(
        session, ObservableCreate(observable_type="domain", data="evil.test"),
        case_id=c2.id, organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await tag_crud.set_tags(session, TaggableType.case, str(c1.id), ["phishing"])
    await tag_crud.set_tags(session, TaggableType.case, str(c2.id), ["phishing", "apt"])
    await session.commit()

    headers = _hdr(analyst_a_token, org_a)
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "merged", "severity": 3}},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    new = resp.json()
    assert new["title"] == "merged"
    assert sorted(new["merged_from"]) == sorted([c1.id, c2.id])
    assert new["merged_into"] is None

    # Children reparented onto the new case.
    tasks = await client.get(f"/api/v1/cases/{new['id']}/tasks", headers=headers)
    assert sorted(t["title"] for t in tasks.json()["items"]) == ["t1", "t2"]
    obs = await client.get(f"/api/v1/cases/{new['id']}/observables", headers=headers)
    assert sorted(o["data"] for o in obs.json()["items"]) == ["1.1.1.1", "evil.test"]

    # Sources frozen + lineage points to the survivor, still readable.
    for src in (c1, c2):
        got = await client.get(f"/api/v1/cases/{src.id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["status"]["stage"] == "duplicated"
        assert got.json()["status"]["label"] == "Duplicated"
        assert got.json()["merged_into"] == new["id"]

    # Tags deduped into the union on the new case.
    async with async_sessionmaker(engine, expire_on_commit=False)() as v:
        rows = (await v.execute(
            select(CaseMerge.source_case_id).where(CaseMerge.target_case_id == new["id"])
        )).scalars().all()
        assert sorted(rows) == sorted([c1.id, c2.id])
        tag_count = len((await v.execute(
            select(Tagging.tag_id).where(
                Tagging.taggable_type == TaggableType.case,
                Tagging.taggable_id == str(new["id"]),
            )
        )).scalars().all())
        assert tag_count == 2  # phishing + apt, deduped


async def test_merge_observable_dedup_unions_flags(
    client: AsyncClient, session, engine, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b")
    await obs_crud.create_case_observable(
        session, ObservableCreate(observable_type="ip", data="9.9.9.9", ioc=True),
        case_id=c1.id, organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await obs_crud.create_case_observable(
        session, ObservableCreate(observable_type="ip", data="9.9.9.9", sighted=True),
        case_id=c2.id, organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    await session.commit()

    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m"}},
        headers=_hdr(analyst_a_token, org_a),
    )
    assert resp.status_code == 201, resp.text
    new_id = resp.json()["id"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as v:
        live = (await v.execute(
            select(Observable).where(
                Observable.case_id == new_id, Observable.deleted_at.is_(None)
            )
        )).scalars().all()
        assert len(live) == 1
        assert live[0].ioc is True and live[0].sighted is True


async def test_merge_rejects_tlp_downgrade(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a", tlp=3)
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b", tlp=2)
    await session.commit()
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m", "tlp": 1}},
        headers=_hdr(analyst_a_token, org_a),
    )
    assert resp.status_code == 422, resp.text


async def test_merge_requires_two_distinct(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    await session.commit()
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c1.id], "case": {"title": "m"}},
        headers=_hdr(analyst_a_token, org_a),
    )
    assert resp.status_code == 400, resp.text


async def test_merge_rejects_mixed_owner_org(
    client: AsyncClient, session, org_a, org_b, builtin_roles,
    analyst_a, analyst_b, analyst_a_token,
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_b, builtin_roles, analyst_b, "b")
    await session.commit()
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m"}},
        headers=_hdr(analyst_a_token, org_a),
    )
    assert resp.status_code == 403, resp.text


async def test_merge_rejects_already_merged_source(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b")
    c3 = await _make_case(session, org_a, builtin_roles, analyst_a, "c")
    await session.commit()
    headers = _hdr(analyst_a_token, org_a)
    first = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m1"}},
        headers=headers,
    )
    assert first.status_code == 201
    again = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c3.id], "case": {"title": "m2"}},
        headers=headers,
    )
    assert again.status_code == 409, again.text


async def test_frozen_source_rejects_writes_allows_reads(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b")
    await session.commit()
    headers = _hdr(analyst_a_token, org_a)
    await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m"}},
        headers=headers,
    )
    # write blocked
    patch = await client.patch(
        f"/api/v1/cases/{c1.id}", json={"description": "x"}, headers=headers
    )
    assert patch.status_code == 409, patch.text
    add_task = await client.post(
        f"/api/v1/cases/{c1.id}/tasks", json={"title": "t"}, headers=headers
    )
    assert add_task.status_code == 409, add_task.text
    # read still works
    assert (await client.get(f"/api/v1/cases/{c1.id}", headers=headers)).status_code == 200


async def test_merge_requires_write_case(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, readonly_a, readonly_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b")
    await session.commit()
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m"}},
        headers=_hdr(readonly_a_token, org_a),
    )
    assert resp.status_code == 403, resp.text


async def test_merge_writes_audit_trail(
    client: AsyncClient, session, engine, org_a, builtin_roles, analyst_a, analyst_a_token
):
    c1 = await _make_case(session, org_a, builtin_roles, analyst_a, "a")
    c2 = await _make_case(session, org_a, builtin_roles, analyst_a, "b")
    await session.commit()
    resp = await client.post(
        "/api/v1/cases/merge",
        json={"source_ids": [c1.id, c2.id], "case": {"title": "m"}},
        headers=_hdr(analyst_a_token, org_a),
    )
    new_id = resp.json()["id"]
    async with async_sessionmaker(engine, expire_on_commit=False)() as v:
        merges = (await v.execute(
            select(Audit).where(Audit.action == "merge")
        )).scalars().all()
        main = [a for a in merges if a.main_action]
        secondary = [a for a in merges if not a.main_action]
        assert len(main) == 1 and main[0].object_id == str(new_id)
        assert sorted(a.object_id for a in secondary) == sorted([str(c1.id), str(c2.id)])
