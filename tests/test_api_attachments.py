"""Tests for blob attachments: file observables and task-log file uploads,
content-addressed dedup, size cap, streaming download, visibility."""
import hashlib

from httpx import AsyncClient

from app.crud import case_ as case_crud
from app.crud import log as log_crud
from app.crud import task as task_crud
from app.models.case_ import CaseCreate
from app.models.log import LogCreate
from app.models.task import TaskCreate


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organisation-Id": org_id}


def _analyzer_headers(secret):
    return {"Authorization": f"Bearer {secret}"}


async def _make_case(session, org, builtin_roles, user):
    return await case_crud.create_case(
        session,
        CaseCreate(title="c"),
        owner_org_id=org.id,
        owner_role_id=builtin_roles["org-admin"].id,
        created_by=str(user.id),
    )


async def test_upload_and_download_file_observable(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    content = b"malware-bytes-\x00\x01\x02"
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables/file",
        files={"file": ("evil.bin", content, "application/octet-stream")},
        data={"observable_type": "file", "ioc": "true"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    obs_id = r.json()["id"]
    assert r.json()["ioc"] is True

    # Download streams the exact bytes back
    d = await client.get(f"/api/v1/observables/{obs_id}/file", headers=h)
    assert d.status_code == 200
    assert d.content == content
    assert "evil.bin" in d.headers["content-disposition"]


async def test_file_analyzer_claim_includes_downloadable_file_ref(
    client: AsyncClient,
    session,
    org_a,
    builtin_roles,
    analyzer_secret,
    observable_types,
    analyst_a,
    analyst_a_token,
):
    connector = {
        "name": "file_analyzer",
        "display_name": "File Analyzer",
        "connector_type": "analyzer",
        "version": "1.0.0",
        "data_types": ["file"],
        "description": "file analyzer",
        "max_runtime_seconds": 5,
    }
    reg = await client.post(
        "/api/internal/analyzer/register",
        json={"connectors": [connector]},
        headers=_analyzer_headers(analyzer_secret),
    )
    assert reg.status_code == 200, reg.text

    h = _headers(analyst_a_token, org_a.id)
    enable = await client.post("/api/v1/connectors/file_analyzer/enable", headers=h)
    assert enable.status_code == 200, enable.text

    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    content = b"file-ref-bytes"
    upload = await client.post(
        f"/api/v1/cases/{case.id}/observables/file",
        files={"file": ("sample.bin", content, "application/octet-stream")},
        data={"observable_type": "file"},
        headers=h,
    )
    assert upload.status_code == 201, upload.text
    obs_id = upload.json()["id"]

    enrich = await client.post(
        f"/api/v1/observables/{obs_id}/enrich",
        json={"connector": "file_analyzer", "force_refresh": True},
        headers=h,
    )
    assert enrich.status_code == 200, enrich.text

    claim = await client.post(
        "/api/internal/analyzer/work",
        params={"connectors": "file_analyzer", "limit": 1},
        headers=_analyzer_headers(analyzer_secret),
    )
    assert claim.status_code == 200, claim.text
    file_ref = claim.json()["items"][0]["file_ref"]
    assert file_ref["attachment_id"]
    assert file_ref["filename"] == "sample.bin"
    assert file_ref["sha256"] == hashlib.sha256(content).hexdigest()
    assert file_ref["size"] == len(content)
    assert file_ref["expires_at"]

    downloaded = await client.get(
        file_ref["download_url"], headers=_analyzer_headers(analyzer_secret)
    )
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert "sample.bin" in downloaded.headers["content-disposition"]


async def test_string_endpoint_rejects_file_type(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    # JSON endpoint still rejects attachment types...
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables",
        json={"observable_type": "file", "data": "x"},
        headers=h,
    )
    assert r.status_code == 422
    # ...and the file endpoint rejects string types
    r2 = await client.post(
        f"/api/v1/cases/{case.id}/observables/file",
        files={"file": ("a.txt", b"hi", "text/plain")},
        data={"observable_type": "ip"},
        headers=h,
    )
    assert r2.status_code == 422


async def test_duplicate_file_in_case_conflicts(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    files = {"file": ("a.bin", b"same-bytes", "application/octet-stream")}
    assert (await client.post(f"/api/v1/cases/{case.id}/observables/file", files=files, data={}, headers=h)).status_code == 201
    # Same content (same sha) -> within-case dedup conflict, even under a different name
    files2 = {"file": ("renamed.bin", b"same-bytes", "application/octet-stream")}
    dup = await client.post(f"/api/v1/cases/{case.id}/observables/file", files=files2, data={}, headers=h)
    assert dup.status_code == 409


async def test_log_attachment_roundtrip(
    client: AsyncClient, session, org_a, builtin_roles, analyst_a, analyst_a_token
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    task = await task_crud.create_task(
        session, TaskCreate(title="t"), case_id=case.id, organisation_id=org_a.id,
        created_by=str(analyst_a.id),
    )
    log = await log_crud.create_log(
        session, LogCreate(message="see attached"), case_id=case.id, task_id=task.id,
        organisation_id=org_a.id, created_by=str(analyst_a.id),
    )
    h = _headers(analyst_a_token, org_a.id)
    base = f"/api/v1/cases/{case.id}/tasks/{task.id}/logs/{log.id}/attachments"
    content = b"pcap-data"
    r = await client.post(
        base,
        files={"file": ("capture.pcap", content, "application/vnd.tcpdump.pcap")},
        data={},
        headers=h,
    )
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]
    assert r.json()["name"] == "capture.pcap"
    assert r.json()["size"] == len(content)
    assert r.json()["public_id"] == f"A-{case.id}-{link_id}"

    lst = await client.get(base, headers=h)
    assert lst.json()["total"] == 1

    d = await client.get(f"{base}/{link_id}/file", headers=h)
    assert d.status_code == 200
    assert d.content == content

    assert (await client.delete(f"{base}/{link_id}", headers=h)).status_code == 204
    assert (await client.get(base, headers=h)).json()["total"] == 0


async def test_blob_dedup_across_owners(
    client: AsyncClient, session, org_a, builtin_roles, observable_types, analyst_a, analyst_a_token
):
    """Same bytes uploaded to two cases create one blob, two links."""
    from sqlmodel import select

    from app.models.attachment import Attachment, ObservableAttachmentLink

    case1 = await _make_case(session, org_a, builtin_roles, analyst_a)
    case2 = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    payload = b"shared-content"
    for case in (case1, case2):
        r = await client.post(
            f"/api/v1/cases/{case.id}/observables/file",
            files={"file": ("f.bin", payload, "application/octet-stream")},
            data={},
            headers=h,
        )
        assert r.status_code == 201, r.text

    blobs = (await session.execute(select(Attachment))).scalars().all()
    links = (await session.execute(select(ObservableAttachmentLink))).scalars().all()
    assert len(blobs) == 1   # deduped by sha256
    assert len(links) == 2   # one reference per observable


async def test_file_observable_not_visible_to_other_org(
    client: AsyncClient,
    session,
    org_a,
    org_b,
    builtin_roles,
    observable_types,
    analyst_a,
    analyst_a_token,
    analyst_b_token,
):
    case = await _make_case(session, org_a, builtin_roles, analyst_a)
    h = _headers(analyst_a_token, org_a.id)
    r = await client.post(
        f"/api/v1/cases/{case.id}/observables/file",
        files={"file": ("x.bin", b"secret", "application/octet-stream")},
        data={},
        headers=h,
    )
    obs_id = r.json()["id"]
    hb = _headers(analyst_b_token, org_b.id)
    assert (await client.get(f"/api/v1/observables/{obs_id}/file", headers=hb)).status_code == 404
