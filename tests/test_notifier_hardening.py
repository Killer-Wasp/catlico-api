"""plans/phase-2 §2.6 — notifier hardening.

Covers: destination URL moved into encrypted secrets (never in NotifierPublic),
the send-time SSRF guard (resolve-and-deny + IP pinning + no redirects), optional
webhook HMAC signing, and the target->secret migration helper.
"""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.crypto import decrypt_secrets, encrypt_secrets
from app.models.notification import (
    Notifier,
    NotifierCreate,
    NotifierType,
    NotifierUpdate,
)
from app.services import net_guard
from app.services.net_guard import (
    SsrfError,
    build_url_secret,
    derive_label,
    guarded_post,
    resolve_and_pin,
    validate_url_shallow,
)


# --- derive_label -----------------------------------------------------------


def test_derive_label_slack_hides_secret_tail():
    label = derive_label("https://hooks.slack.com/services/T00/B00/xxxxSECRET")
    assert label == "hooks.slack.com/services/…"
    assert "SECRET" not in label


def test_derive_label_single_segment():
    assert derive_label("https://example.com/webhook") == "example.com/webhook"


def test_derive_label_no_path():
    assert derive_label("https://example.com") == "example.com"


# --- build_url_secret (migration helper) ------------------------------------


def test_build_url_secret_roundtrip():
    label, blob = build_url_secret("https://hooks.slack.com/services/T/B/xyz", None)
    assert label == "hooks.slack.com/services/…"
    assert decrypt_secrets(blob)["url"] == "https://hooks.slack.com/services/T/B/xyz"


def test_build_url_secret_preserves_existing_secrets():
    existing = encrypt_secrets({"signing_secret": "keepme"})
    label, blob = build_url_secret("https://example.com/hook", existing)
    merged = decrypt_secrets(blob)
    assert merged["url"] == "https://example.com/hook"
    assert merged["signing_secret"] == "keepme"


# --- SSRF guard: rejects -----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost/hook",                        # loopback via DNS
        "http://127.0.0.1/hook",                        # loopback literal
        "http://10.0.0.1/hook",                         # private v4
        "http://[::1]/hook",                            # loopback v6
        "http://[fd00::1]/hook",                         # unique-local v6
        "http://0.0.0.0/hook",                           # unspecified
        "http://[::ffff:169.254.169.254]/",             # v4-mapped metadata bypass
        "http://[::ffff:10.0.0.1]/",                     # v4-mapped private bypass
        "http://[64:ff9b::a00:1]/",                      # NAT64-embedded 10.0.0.1
        "http://100.64.0.1/hook",                        # CGNAT shared space
    ],
)
def test_resolve_and_pin_rejects_internal(url):
    with pytest.raises(SsrfError):
        resolve_and_pin(url)


def test_resolve_and_pin_rejects_bad_scheme():
    with pytest.raises(SsrfError):
        resolve_and_pin("ftp://example.com/hook")
    with pytest.raises(SsrfError):
        resolve_and_pin("file:///etc/passwd")


def test_resolve_and_pin_rejects_dns_rebinding():
    """A public-looking host whose DNS answer is a private IP is rejected — this
    is exactly the address we would have pinned, so pinning closes the window."""
    def rebind(host, port):
        return ["10.0.0.5"]

    with pytest.raises(SsrfError):
        resolve_and_pin("https://evil.example.com/hook", resolver=rebind)


def test_validate_url_shallow_literal_private():
    with pytest.raises(SsrfError):
        validate_url_shallow("http://10.1.2.3/hook")
    with pytest.raises(SsrfError):
        validate_url_shallow("gopher://example.com/")
    # public literal + good scheme passes shallow check
    validate_url_shallow("https://93.184.216.34/hook")


# --- SSRF guard: pins IP, preserves host, refuses redirects -----------------


async def test_guarded_post_pins_ip_and_preserves_host():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["host"] = request.headers.get("Host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await guarded_post(
            client,
            "https://hooks.example.com/services/abc",
            json={"text": "hi"},
            resolver=lambda h, p: ["93.184.216.34"],
        )
    assert resp.status_code == 200
    # Connection dialled the validated IP, but Host + SNI stayed the hostname.
    assert captured["url"].host == "93.184.216.34"
    assert captured["host"] == "hooks.example.com"
    assert captured["sni"] == "hooks.example.com"


async def test_guarded_post_does_not_follow_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        # Redirect to an internal target — must NOT be followed.
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await guarded_post(
            client,
            "https://ok.example.com/hook",
            json={"a": 1},
            resolver=lambda h, p: ["93.184.216.34"],
        )
    assert resp.status_code == 302  # returned as-is, not chased to the metadata IP


async def test_guarded_post_blocked_raises_ssrf():
    async with httpx.AsyncClient() as client:
        with pytest.raises(SsrfError):
            await guarded_post(client, "http://169.254.169.254/", json={})


# --- Secrets contract: URL lives in secrets, never in NotifierPublic --------


async def test_create_notifier_stores_url_in_secrets_not_public(session, org_a):
    from app.api.v1.routes.notifications import _notifier_public
    from app.crud import notification as notif_crud

    notifier = await notif_crud.create_notifier(
        session,
        NotifierCreate(
            type=NotifierType.slack,
            secrets={"url": "https://hooks.slack.com/services/T1/B2/zzzSECRET"},
        ),
        organisation_id=org_a.id,
        created_by="system",
    )
    # URL is encrypted at rest under "url".
    assert decrypt_secrets(notifier.secrets_encrypted)["url"].endswith("zzzSECRET")
    # target became a non-sensitive label; the secret tail is gone.
    assert notifier.target == "hooks.slack.com/services/…"

    public = _notifier_public(notifier)
    dumped = public.model_dump()
    assert dumped["target"] == "hooks.slack.com/services/…"
    assert dumped["has_secrets"] is True
    assert "url" not in dumped
    assert "secrets" not in dumped
    # nothing sensitive survives serialization (the wire shape the web consumes)
    assert "SECRET" not in public.model_dump_json()


async def test_update_notifier_merges_secrets(session, org_a):
    from app.crud import notification as notif_crud

    notifier = await notif_crud.create_notifier(
        session,
        NotifierCreate(
            type=NotifierType.webhook,
            secrets={"url": "https://a.example.com/hook", "signing_secret": "s1"},
        ),
        organisation_id=org_a.id,
        created_by="system",
    )
    # Update only the URL — signing_secret must survive (merge, not overwrite).
    notifier = await notif_crud.update_notifier(
        session,
        notifier,
        NotifierUpdate(secrets={"url": "https://b.example.com/newhook"}),
        updated_by="system",
    )
    merged = decrypt_secrets(notifier.secrets_encrypted)
    assert merged["url"] == "https://b.example.com/newhook"
    assert merged["signing_secret"] == "s1"
    # label re-derived from the new URL
    assert notifier.target == "b.example.com/newhook"

    # null deletes a key
    notifier = await notif_crud.update_notifier(
        session,
        notifier,
        NotifierUpdate(secrets={"signing_secret": None}),
        updated_by="system",
    )
    assert "signing_secret" not in decrypt_secrets(notifier.secrets_encrypted)


# --- Senders read URL from secrets, sign, and are guarded --------------------


def _mk_notifier(org_id, ntype, secrets):
    return Notifier(
        organisation_id=org_id,
        type=ntype,
        target="label-only",
        secrets_encrypted=encrypt_secrets(secrets),
        enabled=True,
        created_by="system",
    )


async def test_send_webhook_reads_url_from_secrets_and_signs(org_a):
    from app.services import notifier_delivery

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["sig"] = request.headers.get("X-Catlico-Signature")
        return httpx.Response(200)

    notifier = _mk_notifier(
        org_a.id,
        NotifierType.webhook,
        {"url": "https://hooks.example.com/wh", "signing_secret": "topsecret"},
    )
    payload = {"event_type": "case.created", "actor": "x"}

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(notifier_delivery, "_client", mock_client), patch.object(
        net_guard, "_resolve", lambda h, p: ["93.184.216.34"]
    ):
        await notifier_delivery._send_webhook(notifier, payload)
    await mock_client.aclose()

    # dialled the pinned IP, not the hostname
    assert "93.184.216.34" in captured["url"]
    # signature is HMAC-SHA256 over the exact bytes sent
    expected = "sha256=" + hmac.new(
        b"topsecret", captured["body"], hashlib.sha256
    ).hexdigest()
    assert captured["sig"] == expected


async def test_send_webhook_no_signature_without_secret(org_a):
    from app.services import notifier_delivery

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sig"] = request.headers.get("X-Catlico-Signature")
        return httpx.Response(200)

    notifier = _mk_notifier(
        org_a.id, NotifierType.webhook, {"url": "https://hooks.example.com/wh"}
    )
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(notifier_delivery, "_client", mock_client), patch.object(
        net_guard, "_resolve", lambda h, p: ["93.184.216.34"]
    ):
        await notifier_delivery._send_webhook(notifier, {"event_type": "e"})
    await mock_client.aclose()
    assert captured["sig"] is None


async def test_send_slack_reads_url_from_secrets(org_a):
    from app.services import notifier_delivery

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    notifier = _mk_notifier(
        org_a.id, NotifierType.slack, {"url": "https://hooks.slack.com/services/T/B/z"}
    )
    payload = {"event_type": "alert.critical", "actor": "sys", "object": {"type": "alert", "id": "1"}}
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(notifier_delivery, "_client", mock_client), patch.object(
        net_guard, "_resolve", lambda h, p: ["93.184.216.34"]
    ):
        await notifier_delivery._send_slack(notifier, payload)
    await mock_client.aclose()
    assert "93.184.216.34" in captured["url"]
    assert "alert.critical" in captured["body"]["text"]


async def test_send_webhook_blocked_url_raises_ssrf(org_a):
    """A notifier pointed at the metadata IP raises SsrfError; the delivery
    consumer's existing except-Exception records it as failed (see
    test_notifier_delivery.test_failed_delivery_records_error)."""
    from app.services import notifier_delivery

    notifier = _mk_notifier(
        org_a.id, NotifierType.webhook, {"url": "http://169.254.169.254/"}
    )
    with pytest.raises(SsrfError):
        await notifier_delivery._send_webhook(notifier, {"event_type": "e"})


async def test_send_webhook_no_url_raises(org_a):
    from app.services import notifier_delivery

    notifier = _mk_notifier(org_a.id, NotifierType.webhook, {"signing_secret": "s"})
    with pytest.raises(ValueError):
        await notifier_delivery._send_webhook(notifier, {"event_type": "e"})


# --- Migration data transform on a seeded row -------------------------------


async def test_migration_helper_on_seeded_row(session, org_a):
    """Seed a pre-migration notifier (plaintext URL in target, no secrets) and
    apply the migration's transform — the URL moves into the encrypted blob and
    target becomes a label. Mirrors upgrade() in the §2.6 migration."""
    from sqlalchemy import text

    notifier = Notifier(
        organisation_id=org_a.id,
        type=NotifierType.webhook,
        target="https://legacy.example.com/services/hook/abc",
        secrets_encrypted=None,
        enabled=True,
        created_by="system",
    )
    session.add(notifier)
    await session.flush()

    # Apply the exact per-row transform the migration runs.
    label, blob = build_url_secret(notifier.target, notifier.secrets_encrypted)
    await session.execute(
        text("UPDATE notifier SET target=:t, secrets_encrypted=:b WHERE id=:i"),
        {"t": label, "b": blob, "i": str(notifier.id)},
    )
    await session.flush()
    await session.refresh(notifier)

    assert notifier.target == "legacy.example.com/services/…"
    assert decrypt_secrets(notifier.secrets_encrypted)["url"] == (
        "https://legacy.example.com/services/hook/abc"
    )
