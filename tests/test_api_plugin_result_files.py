"""Public download surface for plugin-produced file attachments.

Plugin attachments are untrusted third-party content. The download route:
  * rides entity visibility — the *same* guard the plugin-results list route uses,
    so a caller can fetch an attachment iff they can read the owning entity;
  * resolves the ``file_ref`` through ``PluginRunFile`` and only serves it if a
    ``PluginResult`` on the readable entity actually references it (no fetch-by-guess);
  * always serves ``Content-Disposition: attachment`` with a sanitized filename,
    a fixed safe ``application/octet-stream`` content-type (never the plugin's
    declared type), and ``X-Content-Type-Options: nosniff``.
"""
import hashlib
import uuid
from datetime import UTC, datetime

from httpx import AsyncClient

from app.core.storage import get_storage
from app.crud import attachment as attachment_crud
from app.crud import case_ as case_crud
from app.main import app
from app.models.case_ import CaseCreate
from app.models.plugin_runner import PluginDefinition, PluginResult, PluginRunFile


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


async def _make_case(session, org, builtin_roles, user):
    case = await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )
    await session.commit()
    return case


async def _make_observable(client, case_id, token, org_id, data="1.2.3.4"):
    r = await client.post(
        f"/api/v1/cases/{case_id}/observables",
        json={"observable_type": "ip", "data": data},
        headers=_headers(token, org_id),
    )
    assert r.status_code in (200, 201), r.text
    return uuid.UUID(r.json()["id"])


async def _ensure_plugin(session, plugin_id="geoip"):
    if await session.get(PluginDefinition, plugin_id) is None:
        session.add(PluginDefinition(id=plugin_id, created_by="system"))
        await session.commit()


async def _make_run_file(
    session, *, org_id, filename="evidence.bin",
    content_type="application/octet-stream", content=b"plugin evidence bytes",
):
    """Persist a blob + PluginRunFile exactly as the runtime upload route would."""
    storage = app.dependency_overrides[get_storage]()
    sha256 = hashlib.sha256(content).hexdigest()
    await storage.put(sha256, content)
    blob = await attachment_crud.get_or_create_blob(
        session, sha256=sha256, size=len(content),
        content_type=content_type, created_by="test",
    )
    await _ensure_plugin(session)
    run_file = PluginRunFile(
        organisation_id=org_id,
        plugin_id="geoip",
        attachment_id=blob.id,
        filename=filename,
        content_type=content_type,
        size=len(content),
        sha256=sha256,
    )
    session.add(run_file)
    await session.commit()
    return run_file, content


async def _add_result_with_attachment(
    session, *, org_id, entity_type, entity_id, run_file,
):
    file_ref = f"plugin-run-file:{run_file.id}"
    result = PluginResult(
        organisation_id=org_id,
        plugin_id="geoip",
        plugin_version_id="geoip@1.0.0",
        entity_type=entity_type,
        entity_id=entity_id,
        source="plugin",
        render_mode="json",
        fingerprint=f"fp-{uuid.uuid4()}",
        created_at=datetime.now(UTC),
        attachments=[{
            "file_ref": file_ref,
            "filename": run_file.filename,
            "content_type": run_file.content_type,
            "size": run_file.size,
            "sha256": run_file.sha256,
        }],
    )
    session.add(result)
    await session.commit()
    return file_ref


# --- Happy paths across entity surfaces ---


async def test_download_observable_attachment(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    run_file, content = await _make_run_file(session, org_id=org_a.id)
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="observable",
        entity_id=str(obs), run_file=run_file,
    )

    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results/files/{file_ref}",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert r.content == content
    assert r.headers["content-disposition"] == 'attachment; filename="evidence.bin"'
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_download_case_attachment(
    client: AsyncClient, session, org_a, builtin_roles,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    run_file, content = await _make_run_file(session, org_id=org_a.id)
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="case",
        entity_id=str(case.id), run_file=run_file,
    )
    r = await client.get(
        f"/api/v1/cases/{case.id}/plugin-results/files/{file_ref}",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    assert r.content == content
    assert r.headers["content-disposition"].startswith("attachment;")


async def test_download_alert_attachment(
    client: AsyncClient, session, org_a, builtin_roles,
    analyst_a, analyst_a_token,
):
    ha = _headers(analyst_a_token, org_a.id)
    created = await client.post(
        "/api/v1/alerts/",
        json={"type": "phishing", "source": "gw", "source_ref": "e1",
              "title": "a", "description": "d", "severity": 2},
        headers=ha,
    )
    assert created.status_code == 201, created.text
    alert_id = created.json()["id"]
    run_file, content = await _make_run_file(session, org_id=org_a.id)
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="alert",
        entity_id=str(alert_id), run_file=run_file,
    )
    r = await client.get(
        f"/api/v1/alerts/{alert_id}/plugin-results/files/{file_ref}", headers=ha
    )
    assert r.status_code == 200, r.text
    assert r.content == content


# --- Authorisation: same rejection as the entity read ---


async def test_download_denied_to_unrelated_org(
    client: AsyncClient, session, org_a, org_b, builtin_roles, observable_types,
    analyst_a, analyst_a_token, analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    run_file, _ = await _make_run_file(session, org_id=org_a.id)
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="observable",
        entity_id=str(obs), run_file=run_file,
    )
    # org_b cannot read the observable -> same 404 the observable-read route gives.
    assert (
        await client.get(
            f"/api/v1/observables/{obs}", headers=_headers(analyst_b_token, org_b.id)
        )
    ).status_code == 404
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results/files/{file_ref}",
        headers=_headers(analyst_b_token, org_b.id),
    )
    assert r.status_code == 404


async def test_cannot_fetch_by_guessing_ref_on_readable_entity(
    client: AsyncClient, session, org_a, org_b, builtin_roles, observable_types,
    analyst_a, analyst_a_token, analyst_b, analyst_b_token,
):
    """The important one: a caller who CAN read their own entity, and who knows a
    valid file_ref belonging to a result on an entity they CANNOT read, still cannot
    fetch it — the ref must be referenced by a result on the entity in the URL."""
    # org_a's observable carries the secret attachment.
    case_a = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs_a = await _make_observable(client, case_a.id, analyst_a_token, org_a.id)
    run_file, _ = await _make_run_file(session, org_id=org_a.id, content=b"top secret")
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="observable",
        entity_id=str(obs_a), run_file=run_file,
    )

    # org_b has its own readable observable with no such attachment.
    case_b = await _make_case(session, org_b, builtin_roles, analyst_b)
    obs_b = await _make_observable(client, case_b.id, analyst_b_token, org_b.id)

    hb = _headers(analyst_b_token, org_b.id)
    # Guess the ref against org_b's own (readable) entity: not referenced there -> 404.
    assert (
        await client.get(
            f"/api/v1/observables/{obs_b}/plugin-results/files/{file_ref}", headers=hb
        )
    ).status_code == 404
    # Guess the ref against org_a's entity: cannot read it -> 404.
    assert (
        await client.get(
            f"/api/v1/observables/{obs_a}/plugin-results/files/{file_ref}", headers=hb
        )
    ).status_code == 404


async def test_unknown_file_ref_on_readable_entity_is_404(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    # No result / no attachment on this observable at all.
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results/files/plugin-run-file:{uuid.uuid4()}",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 404


# --- Untrusted-content hardening ---


async def test_malicious_filename_does_not_inject_headers(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    # A NUL byte can't reach the DB (Postgres rejects it in a text column), so the
    # storable threats are traversal, an embedded quote, and CR/LF header injection.
    evil = '../../ev"il\r\nX-Injected: yes.bin'
    run_file, _ = await _make_run_file(session, org_id=org_a.id, filename=evil)
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="observable",
        entity_id=str(obs), run_file=run_file,
    )
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results/files/{file_ref}",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    # No injected header leaked out of the filename.
    assert "x-injected" not in {k.lower() for k in r.headers}
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert ".." not in cd
    # Only the two wrapping quotes remain — the embedded quote was stripped.
    assert cd.count('"') == 2
    assert cd.startswith('attachment; filename="')


async def test_plugin_content_type_is_not_reflected(
    client: AsyncClient, session, org_a, builtin_roles, observable_types,
    analyst_a, analyst_a_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    obs = await _make_observable(client, case.id, analyst_a_token, org_a.id)
    run_file, _ = await _make_run_file(
        session, org_id=org_a.id, filename="x.html",
        content_type="text/html", content=b"<script>alert(1)</script>",
    )
    file_ref = await _add_result_with_attachment(
        session, org_id=org_a.id, entity_type="observable",
        entity_id=str(obs), run_file=run_file,
    )
    r = await client.get(
        f"/api/v1/observables/{obs}/plugin-results/files/{file_ref}",
        headers=_headers(analyst_a_token, org_a.id),
    )
    assert r.status_code == 200, r.text
    # The plugin-declared text/html must not be reflected.
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["x-content-type-options"] == "nosniff"
