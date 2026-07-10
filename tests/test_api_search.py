"""Global search: GET /api/v1/search.

Federated Postgres search across cases, alerts, observables, tasks, comments.
Spec: docs/global-search-design.md (catlico workspace root)."""

import uuid

from httpx import AsyncClient

from app.crud import alert as alert_crud
from app.crud import case_ as case_crud
from app.crud import comment as comment_crud
from app.crud import observable as obs_crud
from app.crud import task as task_crud
from app.models.alert import AlertCreate
from app.models.case_ import CaseCreate
from app.models.comment import CommentCreate, CommentEntityType
from app.models.observable import ObservableCreate
from app.models.task import TaskCreate


def _headers(token, org):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org.id}


async def _seed_case(session, org, builtin_roles, created_by, *, title, description=""):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title, description=description),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(created_by),
    )


async def _seed_observable(session, case, org, created_by, *, type_="ip", data, message=""):
    return await obs_crud.create_case_observable(
        session,
        ObservableCreate(observable_type=type_, data=data, message=message),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(created_by),
    )


async def _seed_alert(session, org, created_by, *, title, description="", source_ref=None):
    # app/crud/alert.py has no create_alert — ingestion is upsert-on-dedup-key
    # via ingest_alert, which returns (alert, created).
    alert, _created = await alert_crud.ingest_alert(
        session,
        AlertCreate(
            type="external",
            source="test-siem",
            source_ref=source_ref or f"ref-{uuid.uuid4().hex[:8]}",
            title=title,
            description=description,
        ),
        organisation_id=org.id,
        created_by=str(created_by),
    )
    return alert


async def _seed_task(session, case, org, created_by, *, title, description=""):
    return await task_crud.create_task(
        session,
        TaskCreate(title=title, description=description),
        case_id=case.id,
        organisation_id=org.id,
        created_by=str(created_by),
    )


class TestObservableIpColumn:
    async def test_ip_address_populates_ip(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.1.2.3")
        assert obs.ip == "10.1.2.3"

    async def test_cidr_data_populates_ip_as_network(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.0.0.0/24")
        assert obs.ip == "10.0.0.0/24"

    async def test_netmask_notation_normalises(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.0.0.0/255.0.0.0")
        assert obs.ip == "10.0.0.0/8"

    async def test_garbage_ip_data_leaves_null(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="not-an-ip")
        assert obs.ip is None

    async def test_non_ip_type_leaves_null(self, session, org_a, builtin_roles, admin_user, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        obs = await _seed_observable(session, case, org_a, admin_user.id, type_="domain", data="10.1.2.3")
        assert obs.ip is None


class TestSearchCases:
    async def test_title_match_and_shape(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Phishing campaign targeting finance")
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Malware outbreak")
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "phishing"}, headers=_headers(admin_token, org_a))
        assert r.status_code == 200
        body = r.json()
        assert body["counts"]["case"] == 1
        [hit] = body["results"]["case"]
        assert hit["title"] == "Phishing campaign targeting finance"
        assert "<mark>" in hit["snippet"]

    async def test_prefix_match_while_typing(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Phishing campaign")
        await session.commit()
        r = await client.get("/api/v1/search", params={"q": "phishing camp"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["case"] == 1

    async def test_description_match_ranks_below_title_match(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Weekly review", description="mentions ransomware once")
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Ransomware incident")
        await session.commit()
        r = await client.get("/api/v1/search", params={"q": "ransomware"}, headers=_headers(admin_token, org_a))
        hits = r.json()["results"]["case"]
        assert [h["title"] for h in hits] == ["Ransomware incident", "Weekly review"]

    async def test_no_midword_substring_on_titles(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="Phishing campaign")
        await session.commit()
        r = await client.get("/api/v1/search", params={"q": "ishing"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["case"] == 0

    async def test_tsquery_syntax_is_inert(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="anything")
        await session.commit()
        for q in ["foo & bar |", "!( weird", "a:*b", "it's"]:
            r = await client.get("/api/v1/search", params={"q": q}, headers=_headers(admin_token, org_a))
            assert r.status_code == 200, q

    async def test_short_query_returns_empty_shape(self, client: AsyncClient, org_a, admin_token, admin_user):
        r = await client.get("/api/v1/search", params={"q": "a"}, headers=_headers(admin_token, org_a))
        assert r.status_code == 200
        assert r.json()["counts"] == {"case": 0, "alert": 0, "observable": 0, "task": 0, "comment": 0}

    async def test_long_query_422(self, client: AsyncClient, org_a, admin_token, admin_user):
        r = await client.get("/api/v1/search", params={"q": "x" * 201}, headers=_headers(admin_token, org_a))
        assert r.status_code == 422

    async def test_types_param_limits_buckets(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="phishing")
        await session.commit()
        r = await client.get(
            "/api/v1/search",
            params=[("q", "phishing"), ("types", "alert")],
            headers=_headers(admin_token, org_a),
        )
        body = r.json()
        assert body["counts"]["case"] == 0 and body["results"]["case"] == []

    async def test_pagination(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        for i in range(7):
            await _seed_case(session, org_a, builtin_roles, admin_user.id, title=f"phishing wave {i}")
        await session.commit()
        r = await client.get(
            "/api/v1/search",
            params={"q": "phishing", "limit": 5, "offset": 5},
            headers=_headers(admin_token, org_a),
        )
        body = r.json()
        assert body["counts"]["case"] == 7
        assert len(body["results"]["case"]) == 2


class TestSearchAlertsAndTasks:
    async def test_alert_title_and_source_ref(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_alert(session, org_a, admin_user.id, title="Suspicious login burst")
        await _seed_alert(session, org_a, admin_user.id, title="other", source_ref="SIEM-90210")
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "suspicious login"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["alert"] == 1

        r = await client.get("/api/v1/search", params={"q": "SIEM-90210"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["alert"] == 1

    async def test_task_hit_carries_public_id(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        await _seed_task(session, case, org_a, admin_user.id, title="Review mailbox rules")
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "mailbox"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["task"] == 1
        [hit] = body["results"]["task"]
        assert hit["public_id"] == f"T-{case.id}-{hit['id']}"
