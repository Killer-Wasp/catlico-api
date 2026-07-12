"""Egress policy for the function sandbox (TheHive5 M10).

A function's ``egress`` field is an allowlist of hosts it may reach. This module
is the single place that decides whether an outbound request is permitted:

- Non-globally-routable IPs are ALWAYS blocked — private (RFC1918), loopback,
  link-local (incl. the ``169.254.169.254`` cloud-metadata endpoint), and other
  reserved/multicast/unspecified ranges — even if they appear in the allowlist.
- ``localhost`` (and ``*.localhost``) is blocked by name.
- Otherwise the target host must match an allowlist entry (exact, or a
  subdomain of it; ``*.example.com`` is accepted and treated as ``example.com``).
- An empty allowlist denies everything (default-deny).

The validator is pure and hostname-based. DNS rebinding — a public hostname that
resolves to a private IP — must still be caught at request time by re-checking
the *resolved* address with :func:`is_blocked_ip`; the sandbox runtime is
responsible for that second check.
"""
import ipaddress
import re
from urllib.parse import urlsplit

__all__ = ["check_egress", "is_blocked_ip", "EgressDecision"]


class EgressDecision:
    """Result of an egress check: truthy when allowed, with a human reason."""

    __slots__ = ("allowed", "reason")

    def __init__(self, allowed: bool, reason: str) -> None:
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"EgressDecision(allowed={self.allowed!r}, reason={self.reason!r})"


def _extract_host(target: str) -> str | None:
    """Return the lowercased host of a URL or bare ``host[:port]``, or None."""
    target = (target or "").strip()
    if not target:
        return None
    # Prefix ``//`` for bare hosts so urlsplit populates ``hostname``.
    parsed = urlsplit(target if "//" in target else f"//{target}", scheme="http")
    host = parsed.hostname
    return host.lower() if host else None


def is_blocked_ip(host: str) -> bool:
    """True if ``host`` is an IP literal that is not globally routable (and so
    must never be reached), False if it is a global IP or not an IP at all."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _host_matches(host: str, entry: str) -> bool:
    entry = entry.strip().lower()
    if entry.startswith("*."):
        entry = entry[2:]
    entry = entry.strip(".")
    if not entry:
        return False
    return host == entry or host.endswith("." + entry)


def check_egress(allowlist: str, target: str) -> EgressDecision:
    """Decide whether a function with the given ``allowlist`` may reach ``target``
    (a URL or bare host). See the module docstring for the policy."""
    host = _extract_host(target)
    if host is None:
        return EgressDecision(False, "no host in target")

    if is_blocked_ip(host):
        return EgressDecision(False, "blocked non-global IP address")

    if host == "localhost" or host.endswith(".localhost"):
        return EgressDecision(False, "blocked localhost")

    for entry in re.split(r"[\s,]+", allowlist or ""):
        if entry and _host_matches(host, entry):
            return EgressDecision(True, "allowed by egress allowlist")

    return EgressDecision(False, "host not in egress allowlist")
