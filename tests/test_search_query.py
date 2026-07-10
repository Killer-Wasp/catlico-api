"""Unit tests for search-query classification (app/crud/search.py)."""

from app.crud.search import classify_query


class TestClassifyQuery:
    def test_bare_ip(self):
        c = classify_query("10.1.2.3")
        assert c.net == "10.1.2.3" and c.is_bare_ip

    def test_cidr_prefix(self):
        c = classify_query("10.0.0.0/8")
        assert c.net == "10.0.0.0/8" and not c.is_bare_ip

    def test_cidr_netmask_normalises(self):
        c = classify_query("10.0.0.0/255.0.0.0")
        assert c.net == "10.0.0.0/8" and not c.is_bare_ip

    def test_host_bits_tolerated(self):
        # strict=False: analysts paste "10.0.0.1/24" meaning that host's subnet.
        c = classify_query("10.0.0.1/24")
        assert c.net == "10.0.0.0/24"

    def test_invalid_prefix_falls_through_to_text(self):
        assert classify_query("10.0.0.0/33").net is None

    def test_out_of_range_octet_is_text(self):
        assert classify_query("300.1.1.1").net is None

    def test_plain_text(self):
        c = classify_query("phishing campaign")
        assert c.net is None and not c.is_bare_ip

    def test_ipv6(self):
        c = classify_query("2001:db8::1")
        assert c.net == "2001:db8::1" and c.is_bare_ip

    def test_whitespace_stripped(self):
        assert classify_query("  10.1.2.3  ").net == "10.1.2.3"
