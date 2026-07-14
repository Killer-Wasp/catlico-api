"""SSRF guard + destination-URL helpers for outbound notifier deliveries.

Notifier destinations are attacker-influenced (an org admin types the URL, but a
compromised/rogue admin — or a stored URL whose DNS later changes — can point it
at internal infrastructure). Before we POST to one we:

  * allow only ``http`` / ``https`` schemes;
  * resolve the host to IP(s) **at send time** and reject any private / loopback /
    link-local / multicast / reserved / metadata address (IPv4 *and* IPv6);
  * **pin** the connection to the exact IP we validated (connect to the IP, carry
    the original ``Host`` header + TLS SNI) so a DNS-rebinding attack can't swap
    the address between the check and the connect;
  * never follow redirects (a 30x could bounce us to an internal target).

``derive_label`` / ``build_url_secret`` back the migration that moves the plaintext
``Notifier.target`` URL into the encrypted secrets blob, leaving ``target`` as a
non-sensitive display label.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit

import httpx

_ALLOWED_SCHEMES = {"http", "https"}

# Explicit belt-and-suspenders constants (also covered by the property checks
# below, but called out because they're the classic SSRF targets).
_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),  # AWS/GCP/Azure IMDS
    ipaddress.ip_address("fd00:ec2::254"),     # AWS IMDS over IPv6
}

# Shared address space (CGNAT) — not caught by IPv4Address.is_private.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
# NAT64 well-known prefix: 64:ff9b::/96 embeds an IPv4 in its low 32 bits, so an
# AAAA record here reaches internal v4 through a NAT64 gateway.
_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")


class SsrfError(ValueError):
    """Raised when a URL is refused by the SSRF guard. Subclasses ValueError so
    existing send-time ``except Exception`` handlers record it as a failed
    delivery rather than crashing the outbox drain."""


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if *ip* must never be dialled from a server-side fetch."""
    if ip in _METADATA_IPS:
        return True
    # Unwrap any IPv6 form that embeds an internal IPv4 so it can't hide behind a
    # v6 wrapper: ::ffff:10.0.0.1 (v4-mapped), 2002:a00:1:: (6to4), and
    # 64:ff9b::a00:1 (NAT64, which reaches internal v4 through a NAT64 gateway).
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = None
        if ip.ipv4_mapped is not None:
            embedded = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            embedded = ip.sixtofour
        elif ip in _NAT64_NET:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return _is_blocked_ip(embedded)
    if ip.version == 4 and ip in _CGNAT_NET:  # shared address space (CGNAT)
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        # Backstop: anything not globally routable (future non-global ranges).
        # Does NOT catch NAT64 (is_global=True there) — handled by the unwrap above.
        or not ip.is_global
    )


def _default_resolver(host: str, port: int) -> list[str]:
    """Resolve *host* to the list of IP strings it currently points at."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    # info[4][0] is the address; dedupe while preserving order.
    seen: dict[str, None] = {}
    for info in infos:
        seen.setdefault(info[4][0], None)
    return list(seen)


# Module-level so tests can monkeypatch it to simulate DNS rebinding.
_resolve: Callable[[str, int], list[str]] = _default_resolver


@dataclass(frozen=True)
class PinnedRequest:
    """A request rewritten to connect to a validated IP while preserving the
    original host for routing (``Host`` header) and TLS (``sni_hostname``)."""

    url: str
    host_header: str
    extensions: dict = field(default_factory=dict)


def _reject_literal_private(host: str) -> None:
    """If *host* is a literal IP, reject it when blocked. No DNS here — this is the
    cheap create/update-time check for immediate feedback."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # not a literal IP; the authoritative check happens at send time
    if _is_blocked_ip(ip):
        raise SsrfError(f"destination IP {host} is not a permitted target")


def validate_url_shallow(url: str) -> None:
    """Cheap, DNS-free validation for notifier create/update: enforce the scheme
    allowlist and reject literal private/loopback/etc IPs. The authoritative
    resolve-and-deny still runs at send time (DNS can change afterwards)."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(
            f"URL scheme {parts.scheme!r} is not allowed (use http or https)"
        )
    host = parts.hostname
    if not host:
        raise SsrfError("URL has no host")
    _reject_literal_private(host)


def resolve_and_pin(
    url: str, *, resolver: Callable[[str, int], list[str]] | None = None
) -> PinnedRequest:
    """Validate scheme, resolve *url*'s host, reject any blocked IP, and return a
    :class:`PinnedRequest` bound to the (validated) resolved IP. Raises
    :class:`SsrfError` if the URL is not a permitted target."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(
            f"URL scheme {parts.scheme!r} is not allowed (use http or https)"
        )
    host = parts.hostname
    if not host:
        raise SsrfError("URL has no host")

    default_port = 443 if parts.scheme == "https" else 80
    port = parts.port or default_port

    resolve = resolver or _resolve
    try:
        addresses = resolve(host, port)
    except OSError as exc:
        raise SsrfError(f"could not resolve host {host!r}: {exc}") from exc
    if not addresses:
        raise SsrfError(f"host {host!r} did not resolve to any address")

    pinned_ip: str | None = None
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            raise SsrfError(f"host {host!r} resolved to a non-IP {addr!r}")
        if _is_blocked_ip(ip):
            raise SsrfError(
                f"host {host!r} resolves to blocked address {addr} "
                "(private/loopback/link-local/metadata)"
            )
        if pinned_ip is None:
            pinned_ip = addr

    assert pinned_ip is not None  # non-empty, all-validated => at least one

    # Rewrite the URL to dial the validated IP directly. copy_with brackets IPv6.
    pinned_url = httpx.URL(url).copy_with(host=pinned_ip)
    host_header = f"{host}:{parts.port}" if parts.port else host
    extensions: dict = {}
    if parts.scheme == "https":
        # Keep SNI + cert-hostname verification bound to the real hostname, not
        # the IP we connect to.
        extensions["sni_hostname"] = host
    return PinnedRequest(url=str(pinned_url), host_header=host_header, extensions=extensions)


async def guarded_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: object | None = None,
    content: bytes | None = None,
    headers: dict | None = None,
    resolver: Callable[[str, int], list[str]] | None = None,
) -> httpx.Response:
    """POST to *url* through the SSRF guard: resolve-and-deny, pin the validated
    IP, preserve Host/SNI, and never follow redirects. Raises :class:`SsrfError`
    for a blocked target."""
    pinned = resolve_and_pin(url, resolver=resolver)
    req_headers = dict(headers or {})
    req_headers["Host"] = pinned.host_header
    return await client.post(
        pinned.url,
        json=json,
        content=content,
        headers=req_headers,
        extensions=pinned.extensions,
        follow_redirects=False,
    )


# --- URL -> secret migration helpers ---------------------------------------


def derive_label(url: str) -> str:
    """A non-sensitive display label for a destination URL: host plus a short
    path prefix, e.g. ``https://hooks.slack.com/services/T00/B00/xxx`` ->
    ``hooks.slack.com/services/…``. Never includes the secret path tail."""
    parts = urlsplit(url)
    host = parts.hostname or (url or "").strip()
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return host
    if len(segments) == 1:
        return f"{host}/{segments[0]}"
    return f"{host}/{segments[0]}/…"


def build_url_secret(target: str, existing_blob: str | None) -> tuple[str, str | None]:
    """Migration helper: fold a plaintext ``target`` URL into the encrypted
    secrets blob under ``"url"`` and return ``(display_label, new_blob)``.

    Preserves any secrets already stored (e.g. a signing secret). Imported lazily
    by the Alembic migration so this module's httpx import doesn't load at
    migration-graph build time.
    """
    from app.core.crypto import decrypt_secrets, encrypt_secrets

    merged = decrypt_secrets(existing_blob)
    merged["url"] = target
    return derive_label(target), encrypt_secrets(merged)
