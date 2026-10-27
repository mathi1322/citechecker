import httpx
import pytest

from app.citations import (
    dedupe_citations,
    is_own,
    make_citation,
    normalize_site,
    normalize_url,
    registrable_domain,
    resolve_all,
    resolve_redirect,
)
from app.models import Citation
from tests.mockweb import REDIRECT


class TestRegistrableDomain:
    @pytest.mark.parametrize("value, expected", [
        ("https://www.example.com/about", "example.com"),
        ("example.com", "example.com"),
        ("http://blog.example.com", "example.com"),
        ("https://shop.example.co.uk/x?y=1", "example.co.uk"),
        ("HTTPS://WWW.EXAMPLE.COM", "example.com"),
        ("example.com.", "example.com"),
        ("localhost", "localhost"),
        ("", ""),
    ])
    def test_domains(self, value, expected):
        assert registrable_domain(value) == expected


class TestNormalizeSite:
    @pytest.mark.parametrize("raw, base, domain", [
        ("example.com", "https://example.com", "example.com"),
        ("  Example.com ", "https://example.com", "example.com"),
        ("https://www.example.com/about-us", "https://www.example.com", "example.com"),
        ("http://example.com", "http://example.com", "example.com"),
        ("https://localhost.example.com:8443/x", "https://localhost.example.com:8443", "example.com"),
    ])
    def test_valid(self, raw, base, domain):
        assert normalize_site(raw) == (base, domain)

    @pytest.mark.parametrize("raw, message", [
        ("", "Enter a website"),
        ("   ", "Enter a website"),
        ("not a site", "spaces"),
        ("ftp://example.com", "Only http"),
        ("localhost", "doesn't look like"),
        ("https://", "doesn't look like"),
        ("exa_mple.com", "doesn't look like"),
        ("example.notarealtld", "real domain ending"),
    ])
    def test_invalid(self, raw, message):
        with pytest.raises(ValueError, match=message):
            normalize_site(raw)

    def test_none_input(self):
        with pytest.raises(ValueError):
            normalize_site(None)  # type: ignore[arg-type]


class TestNormalizeUrl:
    def test_strips_tracking_fragment_www_and_trailing_slash(self):
        assert normalize_url("HTTP://WWW.Example.com/Path/?utm_source=x&id=3&gclid=abc#top") == \
            "https://example.com/Path?id=3"

    def test_root_keeps_slash(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_http_and_https_are_equal(self):
        assert normalize_url("http://example.com/a") == normalize_url("https://example.com/a/")


class TestIsOwn:
    def test_www_subdomain_and_apex_match(self):
        assert is_own("https://www.example.com/x", "example.com")
        assert is_own("https://blog.example.com", "example.com")
        assert is_own("example.com", "EXAMPLE.COM")

    def test_lookalike_does_not_match(self):
        assert not is_own("https://example.com.evil.io", "example.com")
        assert not is_own("https://myexample.com", "example.com")

    def test_empty_own_domain(self):
        assert not is_own("https://x.com", "")


class TestMakeCitation:
    def test_redirect_link_uses_domain_title(self):
        c = make_citation(REDIRECT + "abc", "pluralsight.com", 2)
        assert c.domain == "pluralsight.com" and c.position == 2

    def test_redirect_link_with_non_domain_title_keeps_google_domain(self):
        c = make_citation(REDIRECT + "abc", "Some Article Title", 1)
        assert c.domain == "google.com"

    def test_normal_link(self):
        assert make_citation("https://www.g2.com/x", "G2", 1).domain == "g2.com"


def test_dedupe_keeps_first_and_renumbers():
    cites = [
        Citation(url="https://a.com/x", domain="a.com", position=1),
        Citation(url="https://www.a.com/x/?utm_source=y", domain="a.com", position=2),
        Citation(url="https://b.com", domain="b.com", position=3),
    ]
    out = dedupe_citations(cites)
    assert [c.url for c in out] == ["https://a.com/x", "https://b.com"]
    assert [c.position for c in out] == [1, 2]


def test_dedupe_empty():
    assert dedupe_citations([]) == []


class TestResolveRedirect:
    async def test_follows_location(self, web):
        async with web.client() as http:
            assert await resolve_redirect(http, REDIRECT + "rival") == \
                "https://rival.com/guide/best-hands-on-lab-platforms"

    async def test_non_google_url_untouched_and_not_fetched(self, web):
        async with web.client() as http:
            assert await resolve_redirect(http, "https://x.com/a") == "https://x.com/a"
        assert web.requests == []

    async def test_no_location_keeps_original(self, web):
        web.add(REDIRECT + "dead", "gone", status=404)
        async with web.client() as http:
            assert await resolve_redirect(http, REDIRECT + "dead") == REDIRECT + "dead"

    async def test_network_failure_keeps_original(self):
        def boom(request):
            raise httpx.ConnectTimeout("slow")
        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
            assert await resolve_redirect(http, REDIRECT + "x") == REDIRECT + "x"

    async def test_relative_location_is_joined(self, web):
        web.add(REDIRECT + "rel", "", status=301, headers={"location": "/elsewhere"})
        async with web.client() as http:
            assert await resolve_redirect(http, REDIRECT + "rel") == \
                "https://vertexaisearch.cloud.google.com/elsewhere"

    async def test_resolve_all_keeps_order(self, web):
        async with web.client() as http:
            out = await resolve_all(http, [REDIRECT + "g2", "https://plain.com", REDIRECT + "rival"])
        assert out == ["https://www.g2.com/categories/hands-on-labs", "https://plain.com",
                       "https://rival.com/guide/best-hands-on-lab-platforms"]
