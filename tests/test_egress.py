"""Egress policy for the function sandbox (app/services/egress.py)."""
import pytest

from app.services.egress import check_egress, is_blocked_ip


@pytest.mark.parametrize(
    "ip, blocked",
    [
        ("169.254.169.254", True),   # cloud metadata (link-local)
        ("10.0.0.5", True),          # RFC1918
        ("192.168.1.1", True),       # RFC1918
        ("172.16.9.9", True),        # RFC1918
        ("127.0.0.1", True),         # loopback
        ("0.0.0.0", True),           # unspecified
        ("::1", True),               # IPv6 loopback
        ("fe80::1", True),           # IPv6 link-local
        ("fd00::1", True),           # IPv6 ULA (private)
        ("8.8.8.8", False),          # global
        ("1.1.1.1", False),          # global
        ("example.com", False),      # not an IP → not "blocked ip"
    ],
)
def test_is_blocked_ip(ip, blocked):
    assert is_blocked_ip(ip) is blocked


def test_metadata_endpoint_blocked_even_if_allowlisted():
    # Explicitly allow-listing the metadata IP must not grant access.
    d = check_egress("169.254.169.254", "http://169.254.169.254/latest/meta-data/")
    assert not d
    assert "non-global" in d.reason


def test_private_ip_blocked_even_if_allowlisted():
    assert not check_egress("10.0.0.5", "http://10.0.0.5:8080/x")
    assert not check_egress("*", "https://192.168.0.1")


def test_localhost_blocked_by_name():
    assert not check_egress("localhost", "http://localhost:9000")
    assert not check_egress("db.localhost", "http://db.localhost/")


def test_empty_allowlist_denies_everything():
    assert not check_egress("", "https://example.com")
    assert not check_egress("   ", "https://example.com")


def test_exact_and_subdomain_allowlist_match():
    assert check_egress("example.com", "https://example.com/path")
    assert check_egress("example.com", "https://api.example.com/path")
    # A different apex that merely ends with the string is NOT a match.
    assert not check_egress("example.com", "https://notexample.com")
    assert not check_egress("example.com", "https://example.com.evil.com")


def test_wildcard_entry_treated_as_domain():
    assert check_egress("*.example.com", "https://api.example.com")
    assert check_egress("*.example.com", "https://example.com")


def test_allowlist_separators_and_multiple_entries():
    allow = "example.com, api.other.net\nthird.org"
    assert check_egress(allow, "https://third.org")
    assert check_egress(allow, "https://x.api.other.net")
    assert not check_egress(allow, "https://unlisted.net")


def test_global_ip_allowed_only_when_listed():
    assert check_egress("8.8.8.8", "https://8.8.8.8")
    assert not check_egress("1.1.1.1", "https://8.8.8.8")


def test_bare_host_and_invalid_targets():
    assert check_egress("example.com", "example.com")
    assert not check_egress("example.com", "")
    assert not check_egress("example.com", "   ")
