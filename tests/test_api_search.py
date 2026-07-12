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


async def _seed_case(session, org, builtin_roles, created_by, *, title, description="", summary=None):
    return await case_crud.create_case(
        session,
        CaseCreate(title=title, description=description, summary=summary),
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

    async def test_summary_only_match_is_highlighted(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(
            session, org_a, builtin_roles, admin_user.id,
            title="Weekly review", description="", summary="root cause was a beaconing implant",
        )
        await session.commit()
        r = await client.get("/api/v1/search", params={"q": "beaconing"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["case"] == 1
        assert "<mark>" in body["results"]["case"][0]["snippet"]

    async def test_tsquery_syntax_is_inert(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="anything")
        await session.commit()
        for q in ["foo & bar |", "!( weird", "a:*b", "it's"]:
            r = await client.get("/api/v1/search", params={"q": q}, headers=_headers(admin_token, org_a))
            assert r.status_code == 200, q

    async def test_control_characters_never_reach_the_driver(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        """A NUL byte is not valid UTF-8 to asyncpg and raises at bind time; no
        input may produce a 500."""
        await _seed_case(session, org_a, builtin_roles, admin_user.id, title="phishing")
        await session.commit()
        for q in ["a\x00b", "phish\x00ing", "\x01\x02phishing", "phishing\x07"]:
            r = await client.get("/api/v1/search", params={"q": q}, headers=_headers(admin_token, org_a))
            assert r.status_code == 200, repr(q)
        # Stripping is not silently destructive: the term still matches.
        r = await client.get("/api/v1/search", params={"q": "phish\x00ing"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["case"] == 1

    async def test_control_characters_only_query_is_empty_not_500(self, client: AsyncClient, org_a, admin_token, admin_user):
        """Sanitizing down to <2 chars must hit the empty-shape short circuit."""
        r = await client.get("/api/v1/search", params={"q": "\x00\x00\x00"}, headers=_headers(admin_token, org_a))
        assert r.status_code == 200
        assert r.json()["counts"]["case"] == 0

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


async def _seed_comment(session, org, created_by, *, entity_type, entity_id, message):
    return await comment_crud.create_comment(
        session,
        CommentCreate(message=message),
        entity_type=entity_type,
        entity_id=str(entity_id),
        organisation_id=org.id,
        created_by=str(created_by),
    )


class TestSearchAlertsAndTasks:
    async def test_alert_title_and_source_ref(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        await _seed_alert(session, org_a, admin_user.id, title="Suspicious login burst")
        await _seed_alert(session, org_a, admin_user.id, title="other", source_ref="SIEM-90210")
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "suspicious login"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["alert"] == 1

        r = await client.get("/api/v1/search", params={"q": "SIEM-90210"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["alert"] == 1
        # A source_ref-only match must still be highlighted in the snippet.
        assert "<mark>" in body["results"]["alert"][0]["snippet"]

    async def test_task_hit_carries_public_id(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        await _seed_task(session, case, org_a, admin_user.id, title="Review mailbox rules")
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "mailbox"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["task"] == 1
        [hit] = body["results"]["task"]
        assert hit["public_id"] == f"T-{case.id}-{hit['id']}"


class TestSearchComments:
    async def test_comment_body_match_with_author(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="c")
        await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.case, entity_id=case.id,
            message="looks like the same phish kit as last month",
        )
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "phish kit"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["comment"] == 1
        [hit] = body["results"]["comment"]
        assert hit["entity_type"] == "case" and hit["entity_id"] == str(case.id)
        assert "<mark>" in hit["snippet"]
        assert hit["author_name"] != ""


class TestSearchObservables:
    async def _seed(self, session, org_a, builtin_roles, admin_user):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="infra case")
        await _seed_observable(session, case, org_a, admin_user.id, data="10.0.1.5")
        await _seed_observable(session, case, org_a, admin_user.id, data="10.0.0.0/24")
        await _seed_observable(session, case, org_a, admin_user.id, data="192.168.7.7")
        await _seed_observable(
            session, case, org_a, admin_user.id, type_="url",
            data="http://10.0.1.5/malware.bin",
        )
        await _seed_observable(
            session, case, org_a, admin_user.id, type_="hash",
            data="D41D8CD98F00B204E9800998ECF8427E".lower(),
        )
        await session.commit()
        return case

    async def test_cidr_finds_contained_ips_and_subranges(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        await self._seed(session, org_a, builtin_roles, admin_user)
        r = await client.get("/api/v1/search", params={"q": "10.0.0.0/16"}, headers=_headers(admin_token, org_a))
        data = {h["data"] for h in r.json()["results"]["observable"]}
        assert "10.0.1.5" in data and "10.0.0.0/24" in data
        assert "192.168.7.7" not in data

    async def test_netmask_notation(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        await self._seed(session, org_a, builtin_roles, admin_user)
        r = await client.get(
            "/api/v1/search", params={"q": "10.0.0.0/255.255.0.0"}, headers=_headers(admin_token, org_a)
        )
        assert r.json()["counts"]["observable"] >= 2

    async def test_bare_ip_finds_exact_containing_range_and_url(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        await self._seed(session, org_a, builtin_roles, admin_user)
        r = await client.get("/api/v1/search", params={"q": "10.0.0.77"}, headers=_headers(admin_token, org_a))
        data = {h["data"] for h in r.json()["results"]["observable"]}
        assert data == {"10.0.0.0/24"}  # inside the stored /24; no exact/url match

        r = await client.get("/api/v1/search", params={"q": "10.0.1.5"}, headers=_headers(admin_token, org_a))
        data = {h["data"] for h in r.json()["results"]["observable"]}
        assert "10.0.1.5" in data  # exact
        assert "http://10.0.1.5/malware.bin" in data  # substring inside URL

    async def test_uppercase_hash_fragment_matches(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        await self._seed(session, org_a, builtin_roles, admin_user)
        r = await client.get("/api/v1/search", params={"q": "8CD98F00B2"}, headers=_headers(admin_token, org_a))
        data = {h["data"] for h in r.json()["results"]["observable"]}
        assert "d41d8cd98f00b204e9800998ecf8427e" in data

    async def test_grouped_mode(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        case1 = await self._seed(session, org_a, builtin_roles, admin_user)
        case2 = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="second case")
        await _seed_observable(session, case2, org_a, admin_user.id, data="10.0.1.5")
        await session.commit()

        r = await client.get(
            "/api/v1/search",
            params={"q": "10.0.1.5", "group_observables": "true"},
            headers=_headers(admin_token, org_a),
        )
        body = r.json()
        assert body["results"]["observable"] == []
        groups = {g["data"]: g["occurrences"] for g in body["results"]["observable_groups"]}
        assert groups["10.0.1.5"] == 2
        # counts stay per-occurrence: 2x 10.0.1.5 + 1 url containing it
        assert body["counts"]["observable"] == 3


class TestSearchVisibility:
    """The security gate: search must never surface a row the acting org's list
    views would hide. Every test seeds under org_a and searches as org_b (or
    proves soft-deleted rows drop out), trying to break isolation."""

    async def test_org_b_sees_nothing_of_org_a(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token, observable_types):
        # A superadmin token scoped (via X-Organisation-Id) to org_b must not
        # see ANY of org_a's entities — text query, bare-IP query, or CIDR query
        # (the inet path is separate SQL and must apply the same filter).
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="secret phishing case")
        await _seed_observable(session, case, org_a, admin_user.id, data="10.9.9.9")
        await _seed_task(session, case, org_a, admin_user.id, title="secret phishing task")
        await _seed_alert(session, org_a, admin_user.id, title="secret phishing alert")
        await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.case, entity_id=case.id,
            message="secret phishing comment",
        )
        await session.commit()

        for q in ["phishing", "10.9.9.9", "10.9.0.0/16"]:
            r = await client.get("/api/v1/search", params={"q": q}, headers=_headers(admin_token, org_b))
            body = r.json()
            # counts must be zero too: a non-zero count with empty results still
            # tells org_b that org_a has a matching row.
            assert body["counts"] == {
                "case": 0, "alert": 0, "observable": 0, "task": 0, "comment": 0,
            }, f"leak for query {q!r}: {body['counts']}"
            assert body["results"]["observable_groups"] == [], f"group leak for {q!r}"

    async def test_org_b_grouped_observables_no_leak(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token, observable_types):
        # The grouped (palette) observable path is separate SQL from the
        # ungrouped path — it must apply the same visibility filter.
        case1 = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="grouped infra one")
        case2 = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="grouped infra two")
        await _seed_observable(session, case1, org_a, admin_user.id, data="10.8.8.8")
        await _seed_observable(session, case2, org_a, admin_user.id, data="10.8.8.8")
        await session.commit()

        # Sanity: org_a's grouped view does see both occurrences.
        r = await client.get(
            "/api/v1/search",
            params={"q": "10.8.8.8", "group_observables": "true"},
            headers=_headers(admin_token, org_a),
        )
        body = r.json()
        assert body["counts"]["observable"] == 2
        assert body["results"]["observable_groups"][0]["occurrences"] == 2

        r = await client.get(
            "/api/v1/search",
            params={"q": "10.8.8.8", "group_observables": "true"},
            headers=_headers(admin_token, org_b),
        )
        body = r.json()
        assert body["counts"]["observable"] == 0
        assert body["results"]["observable_groups"] == []

    async def test_shared_case_visible_to_recipient_org(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token):
        # A case shared to org_b (non-owner share) must appear in org_b's search.
        from app.models.case_share import CaseShare

        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="jointly worked phishing case")
        session.add(CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            is_owner=False,
            role_id=builtin_roles["analyst"].id,
            created_by=str(admin_user.id),
        ))
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "jointly worked"}, headers=_headers(admin_token, org_b))
        assert r.json()["counts"]["case"] == 1

    async def test_shared_case_comment_visible_but_task_and_observable_hidden(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token, observable_types):
        # Comments ride the case's ANY-share visibility, so a non-owner share
        # exposes case comments. But a mere case share does NOT expose the
        # case's tasks/observables — those need ownership or an explicit share.
        from app.models.case_share import CaseShare

        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="cross-org zebra case")
        await _seed_task(session, case, org_a, admin_user.id, title="zebra remediation task")
        await _seed_observable(session, case, org_a, admin_user.id, data="10.6.6.6")
        await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.case, entity_id=case.id,
            message="zebra kit noted here",
        )
        session.add(CaseShare(
            case_id=case.id,
            organisation_id=org_b.id,
            is_owner=False,
            role_id=builtin_roles["analyst"].id,
            created_by=str(admin_user.id),
        ))
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "zebra"}, headers=_headers(admin_token, org_b))
        body = r.json()
        assert body["counts"]["case"] == 1
        assert body["counts"]["comment"] == 1
        assert body["counts"]["task"] == 0, "shared case must not expose its tasks"

        r = await client.get("/api/v1/search", params={"q": "10.6.6.6"}, headers=_headers(admin_token, org_b))
        assert r.json()["counts"]["observable"] == 0, "shared case must not expose its observables"

    async def test_alert_comment_does_not_leak(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token):
        # A comment on org_a's ALERT (alerts are org-owned, never shared) must
        # not appear for org_b.
        alert = await _seed_alert(session, org_a, admin_user.id, title="alert with a note")
        await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.alert, entity_id=alert.id,
            message="giraffe indicator on this alert",
        )
        await session.commit()

        # Sanity: org_a sees its own alert comment.
        r = await client.get("/api/v1/search", params={"q": "giraffe"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["comment"] == 1

        r = await client.get("/api/v1/search", params={"q": "giraffe"}, headers=_headers(admin_token, org_b))
        assert r.json()["counts"]["comment"] == 0

    async def test_task_share_grants_task_but_not_observable(self, client: AsyncClient, session, org_a, org_b, builtin_roles, admin_user, admin_token, observable_types):
        # A task on org_a's case, explicitly shared to org_b via TaskShare, IS
        # returned in org_b's task bucket — while the case's observables (no
        # ObservableShare) stay hidden. Pins the ownership/share asymmetry.
        from app.models.task_share import TaskShare

        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="antelope case")
        task = await _seed_task(session, case, org_a, admin_user.id, title="antelope lateral movement")
        await _seed_observable(session, case, org_a, admin_user.id, data="10.5.5.5")
        session.add(TaskShare(
            case_id=case.id,
            task_id=task.id,
            organisation_id=org_b.id,
            created_by=str(admin_user.id),
        ))
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "antelope"}, headers=_headers(admin_token, org_b))
        body = r.json()
        assert body["counts"]["task"] == 1, "task shared via TaskShare must be visible"
        assert body["counts"]["case"] == 0, "no CaseShare — parent case stays hidden"

        r = await client.get("/api/v1/search", params={"q": "10.5.5.5"}, headers=_headers(admin_token, org_b))
        assert r.json()["counts"]["observable"] == 0, "no ObservableShare — observable stays hidden"

    async def test_soft_deleted_case_excluded(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="deleted phishing case")
        await session.commit()
        await case_crud.delete_case(
            session, case, deleted_by=str(admin_user.id), organisation_id=org_a.id
        )
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "deleted phishing"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["case"] == 0

    async def test_comment_on_soft_deleted_case_excluded(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token):
        """A live comment must not outlive its case: search would otherwise be
        the one surface where a deleted case's text is still readable."""
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="doomed case")
        await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.case, entity_id=case.id,
            message="phishing kit analysis notes",
        )
        await session.commit()
        # Sanity: visible while the case lives.
        r = await client.get("/api/v1/search", params={"q": "phishing kit"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["comment"] == 1

        await case_crud.delete_case(
            session, case, deleted_by=str(admin_user.id), organisation_id=org_a.id
        )
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "phishing kit"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["comment"] == 0

    async def test_soft_deleted_observable_task_alert_comment_excluded(self, client: AsyncClient, session, org_a, builtin_roles, admin_user, admin_token, observable_types):
        # One combined soft-delete test across the remaining entity types, each
        # via its real crud delete helper.
        case = await _seed_case(session, org_a, builtin_roles, admin_user.id, title="kangaroo case")
        obs = await _seed_observable(session, case, org_a, admin_user.id, data="10.4.4.4")
        task = await _seed_task(session, case, org_a, admin_user.id, title="kangaroo task")
        alert = await _seed_alert(session, org_a, admin_user.id, title="kangaroo alert")
        comment = await _seed_comment(
            session, org_a, admin_user.id,
            entity_type=CommentEntityType.case, entity_id=case.id,
            message="kangaroo comment",
        )
        await session.commit()

        # Sanity before deletion: everything is findable.
        r = await client.get("/api/v1/search", params={"q": "kangaroo"}, headers=_headers(admin_token, org_a))
        pre = r.json()["counts"]
        assert pre["task"] == 1 and pre["alert"] == 1 and pre["comment"] == 1
        r = await client.get("/api/v1/search", params={"q": "10.4.4.4"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["observable"] == 1

        await obs_crud.delete_observable(session, obs, deleted_by=str(admin_user.id))
        await task_crud.delete_task(session, task, deleted_by=str(admin_user.id))
        await alert_crud.delete_alert(session, alert, deleted_by=str(admin_user.id))
        await comment_crud.delete_comment(session, comment, deleted_by=str(admin_user.id))
        await session.commit()

        r = await client.get("/api/v1/search", params={"q": "kangaroo"}, headers=_headers(admin_token, org_a))
        body = r.json()
        assert body["counts"]["task"] == 0
        assert body["counts"]["alert"] == 0
        assert body["counts"]["comment"] == 0
        r = await client.get("/api/v1/search", params={"q": "10.4.4.4"}, headers=_headers(admin_token, org_a))
        assert r.json()["counts"]["observable"] == 0
