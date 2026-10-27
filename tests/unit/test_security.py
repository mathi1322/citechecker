"""SSRF guard (no fetching internal addresses) and robots.txt politeness for other sites' pages."""

import httpx
import pytest

from app.fetch import BlockedURL, RobotsCache, check_public, fetch, make_client, polite_fetch
from app.fetch import resolve as REAL_RESOLVE
from app.jobs import features_for
from app.technical import load_sitemap
from tests.conftest import TODAY


def ok_transport():
    return httpx.MockTransport(lambda r: httpx.Response(200, text="<p>hi</p>"))


class TestGuard:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/", "http://10.1.2.3/admin", "http://169.254.169.254/latest/meta-data/",
        "http://192.168.0.1/", "http://[::1]/", "http://0.0.0.0/",
        "http://metadata.internal/", "http://db.internal:5432/", "http://mixed.internal/",
        "http://v6.internal/", "http://mapped.internal/", "http://nodns.internal/",
    ])
    async def test_internal_addresses_blocked(self, url):
        async with make_client(transport=ok_transport()) as http:
            res = await fetch(http, url)
        assert res.error == "blocked" and res.status is None

    async def test_public_address_allowed(self):
        async with make_client(transport=ok_transport()) as http:
            assert (await fetch(http, "https://example.com/")).ok

    async def test_redirect_to_internal_blocked(self):
        def handler(request):
            if request.url.host == "public.com":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
            return httpx.Response(200, text="secret")
        async with make_client(transport=httpx.MockTransport(handler)) as http:
            res = await fetch(http, "https://public.com/go")
        assert res.error == "blocked" and "secret" not in res.text

    async def test_dns_failure_is_connect_error(self):
        async with make_client(transport=ok_transport()) as http:
            res = await fetch(http, "http://unresolvable.internal/")
        assert res.error == "connect: ConnectError"

    async def test_non_http_scheme_blocked(self):
        with pytest.raises(BlockedURL, match="scheme"):
            await check_public(httpx.Request("GET", "ftp://example.com/file"))

    async def test_guard_can_be_turned_off(self):
        async with make_client(transport=ok_transport(), guard=False) as http:
            assert (await fetch(http, "http://10.0.0.1/")).ok

    async def test_sitemap_pointing_inside_is_ignored(self, web):
        web.add("https://evil-sitemap.com/sitemap.xml",
                "<sitemapindex><sitemap><loc>http://metadata.internal/s.xml</loc></sitemap></sitemapindex>")
        async with web.client() as http:
            check, urls = await load_sitemap(http, "https://evil-sitemap.com", [])
        assert urls == [] and check.status == "pass"
        assert not any(r.url.host == "metadata.internal" for r in web.requests)

    async def test_real_resolver_handles_ip_literals(self, monkeypatch):
        # the fake DNS fixture is bypassed for IP literals: they're checked directly
        async def explode(host):
            raise AssertionError("IP literals must not hit DNS")
        monkeypatch.setattr("app.fetch.resolve", explode)
        with pytest.raises(BlockedURL):
            await check_public(httpx.Request("GET", "http://10.0.0.1/"))


async def test_real_resolver_reads_local_hosts_file():
    # REAL_RESOLVE was imported before the fake-DNS fixture patched app.fetch.resolve.
    # "localhost" comes from the hosts file, so this needs no network.
    assert any(a in ("127.0.0.1", "::1") for a in await REAL_RESOLVE("localhost"))


class TestRobots:
    async def test_disallowed_page_is_skipped(self, web):
        web.add("https://rival.com/robots.txt", "User-agent: CiteCheckBot\nDisallow: /guide/\n",
                headers={"content-type": "text/plain"})
        async with web.client() as http:
            robots = RobotsCache(http)
            res = await polite_fetch(http, robots, "https://rival.com/guide/best-hands-on-lab-platforms")
            allowed = await polite_fetch(http, robots, "https://rival.com/other")
        assert res.error == "robots" and allowed.status == 404
        assert not any(str(r.url) == "https://rival.com/guide/best-hands-on-lab-platforms" for r in web.requests)
        assert sum(1 for r in web.requests if r.url.path == "/robots.txt") == 1  # cached per site

    async def test_wildcard_disallow_applies(self, web):
        web.add("https://shy.com/robots.txt", "User-agent: *\nDisallow: /\n")
        async with web.client() as http:
            assert not await RobotsCache(http).allowed("https://shy.com/page")

    async def test_missing_or_empty_robots_allows(self, web):
        web.add("https://empty.com/robots.txt", "   ")
        async with web.client() as http:
            robots = RobotsCache(http)
            assert await robots.allowed("https://no-robots.com/page")
            assert await robots.allowed("https://empty.com/page")

    async def test_other_bots_rules_dont_apply(self, web):
        web.add("https://picky.com/robots.txt", "User-agent: GPTBot\nDisallow: /\n")
        async with web.client() as http:
            assert await RobotsCache(http).allowed("https://picky.com/page")


def test_skip_reasons_shown_in_features():
    from app.fetch import FetchResult
    f = features_for(FetchResult(url="u", final_url="u", status=None, error="robots"), "q", TODAY)
    assert not f.ok and "robots.txt" in f.error
    f = features_for(FetchResult(url="u", final_url="u", status=None, error="blocked"), "q", TODAY)
    assert "not a public web address" in f.error
