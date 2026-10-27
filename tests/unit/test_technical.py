import httpx

from app.fetch import FetchResult, fetch, make_client
from app.technical import check_page, check_robots, load_sitemap, page_problems, parse_sitemap, visible_word_count
from tests.mockweb import FILLER, page

BASE = "https://acme-labs.com"


def robots(text="", status=200, error=None):
    return FetchResult(url=BASE + "/robots.txt", final_url=BASE + "/robots.txt", status=status, text=text,
                       error=error)


def by_id(checks):
    return {c.id: c for c in checks}


class TestRobots:
    def test_missing_robots_allows_all(self):
        checks, sitemaps = check_robots(robots(status=404, error="http_error"), BASE)
        assert checks[0].status == "pass" and "every crawler" in checks[0].detail and sitemaps == []

    def test_unreachable_robots_warns(self):
        checks, _ = check_robots(robots(status=None, error="timeout"), BASE)
        assert checks[0].status == "warn"

    def test_robots_server_error_fails(self):
        checks, _ = check_robots(robots(status=503, error="http_error"), BASE)
        assert checks[0].status == "fail" and "503" in checks[0].detail

    def test_disallow_all_blocks_search_bots(self):
        checks, _ = check_robots(robots("User-agent: *\nDisallow: /\n"), BASE)
        c = by_id(checks)
        assert c["robots-search-bots"].status == "fail"
        assert "Googlebot" in c["robots-search-bots"].detail
        assert c["robots-training-bots"].status == "warn"

    def test_per_bot_rules_and_sitemap(self, web):
        text = web.routes["GET https://acme-labs.com/robots.txt"][2]
        checks, sitemaps = check_robots(robots(text), BASE)
        c = by_id(checks)
        assert c["robots-search-bots"].status == "fail"
        assert "PerplexityBot" in c["robots-search-bots"].detail
        assert "GPTBot" not in c["robots-search-bots"].detail
        assert "GPTBot" in c["robots-training-bots"].detail
        assert sitemaps == ["https://acme-labs.com/sitemap.xml"]

    def test_only_training_bots_blocked(self):
        checks, _ = check_robots(robots("User-agent: GPTBot\nDisallow: /\n\nUser-agent: CCBot\nDisallow: /"), BASE)
        c = by_id(checks)
        assert c["robots-search-bots"].status == "pass"
        assert c["robots-training-bots"].status == "warn"

    def test_everything_allowed(self):
        c = by_id(check_robots(robots("User-agent: *\nAllow: /\n"), BASE)[0])
        assert c["robots-search-bots"].status == "pass" and c["robots-training-bots"].status == "pass"

    def test_malformed_robots_is_ignored(self):
        c = by_id(check_robots(robots("<html>this is not robots</html>\n:::\nDisallow"), BASE)[0])
        assert c["robots-search-bots"].status == "pass"


class TestSitemap:
    def test_parse_urlset(self):
        pages, children = parse_sitemap("<urlset><url><loc> https://a.com/x </loc></url></urlset>")
        assert pages == ["https://a.com/x"] and children == []

    def test_parse_index(self):
        pages, children = parse_sitemap("<sitemapindex><sitemap><loc>https://a.com/s1.xml</loc></sitemap></sitemapindex>")
        assert pages == [] and children == ["https://a.com/s1.xml"]

    async def test_listed_sitemap_loaded(self, web):
        async with web.client() as http:
            check, urls = await load_sitemap(http, BASE, ["https://acme-labs.com/sitemap.xml"])
        assert check.status == "pass" and len(urls) == 3

    async def test_default_location_used_when_none_listed(self, web):
        async with web.client() as http:
            check, urls = await load_sitemap(http, BASE, [])
        assert check.status == "pass" and "https://acme-labs.com/pricing" in urls

    async def test_missing_sitemap_warns(self, web):
        async with web.client() as http:
            check, urls = await load_sitemap(http, "https://nositemap.com", [])
        assert check.status == "warn" and urls == []

    async def test_html_404_page_is_not_a_sitemap(self, web):
        web.add("https://soft404.com/sitemap.xml", "<html>Page not found</html>")
        async with web.client() as http:
            check, _ = await load_sitemap(http, "https://soft404.com", [])
        assert check.status == "warn"

    async def test_sitemap_index_with_children_and_dedupe(self, web):
        web.add("https://big.com/sitemap.xml",
                "<sitemapindex><sitemap><loc>https://big.com/a.xml</loc></sitemap>"
                "<sitemap><loc>https://big.com/b.xml</loc></sitemap>"
                "<sitemap><loc>https://big.com/broken.xml</loc></sitemap></sitemapindex>")
        web.add("https://big.com/a.xml", "<urlset><url><loc>https://big.com/1</loc></url></urlset>")
        web.add("https://big.com/b.xml", "<urlset><url><loc>https://big.com/1</loc></url>"
                                         "<url><loc>https://big.com/2</loc></url></urlset>")
        async with web.client() as http:
            check, urls = await load_sitemap(http, "https://big.com", [])
        assert urls == ["https://big.com/1", "https://big.com/2"] and check.status == "pass"

    async def test_max_urls_cap(self, web):
        locs = "".join(f"<url><loc>https://cap.com/{i}</loc></url>" for i in range(50))
        web.add("https://cap.com/sitemap.xml", f"<urlset>{locs}</urlset>")
        web.add("https://cap.com/idx.xml", "<sitemapindex><sitemap><loc>https://cap.com/sitemap.xml</loc>"
                                           "</sitemap><sitemap><loc>https://cap.com/sitemap.xml</loc></sitemap>"
                                           "</sitemapindex>")
        async with web.client() as http:
            _, urls = await load_sitemap(http, "https://cap.com", ["https://cap.com/idx.xml"], max_urls=10)
        assert len(urls) == 10


def fetched(html, status=200, headers=None, url=BASE + "/", final=None, redirects=0, error=None):
    return FetchResult(url=url, final_url=final or url, status=status, headers=headers or {}, text=html,
                       redirects=redirects, error=error)


GOOD = page("Good", f"<main><p>{FILLER * 5}</p></main>", head=f'<link rel="canonical" href="{BASE}/">')


class TestCheckPage:
    def test_healthy_page(self):
        c = by_id(check_page(fetched(GOOD), "Homepage"))
        assert {k: v.status for k, v in c.items()} == {
            "status": "pass", "noindex": "pass", "canonical": "pass", "js-content": "pass"}
        assert c["status"].label == "Homepage: loads"

    def test_redirect_loop(self):
        checks = check_page(fetched("", status=None, error="redirect_loop"))
        assert checks[0].status == "fail" and "loop" in checks[0].detail and len(checks) == 1

    def test_unreachable(self):
        assert check_page(fetched("", status=None, error="timeout"))[0].detail == "The page didn't load (timeout)."

    def test_http_error(self):
        assert "HTTP 500" in check_page(fetched("", status=500))[0].detail

    def test_many_redirects_warn(self):
        assert by_id(check_page(fetched(GOOD, redirects=3)))["redirects"].status == "warn"

    def test_noindex_meta(self):
        html = page("x", f"<p>{FILLER * 5}</p>", head='<meta name="robots" content="NOINDEX, follow">')
        c = by_id(check_page(fetched(html)))["noindex"]
        assert c.status == "fail" and "meta tag" in c.detail

    def test_noindex_googlebot_meta(self):
        html = page("x", f"<p>{FILLER * 5}</p>", head='<meta name="googlebot" content="noindex">')
        assert by_id(check_page(fetched(html)))["noindex"].status == "fail"

    def test_noindex_header(self):
        c = by_id(check_page(fetched(GOOD, headers={"x-robots-tag": "noindex"})))["noindex"]
        assert c.status == "fail" and "X-Robots-Tag" in c.detail

    def test_canonical_missing(self):
        html = page("x", f"<p>{FILLER * 5}</p>")
        assert by_id(check_page(fetched(html)))["canonical"].status == "warn"

    def test_canonical_elsewhere(self):
        html = page("x", f"<p>{FILLER * 5}</p>", head='<link rel="canonical" href="https://other.com/">')
        c = by_id(check_page(fetched(html)))["canonical"]
        assert c.status == "warn" and "other.com" in c.detail

    def test_relative_canonical_to_self(self):
        html = page("x", f"<p>{FILLER * 5}</p>", head='<link rel="canonical" href="/labs/">')
        c = by_id(check_page(fetched(html, url=BASE + "/labs")))["canonical"]
        assert c.status == "pass"

    def test_js_only_page(self):
        html = page("App", '<div id="root"></div><script src="/bundle.js"></script>')
        c = by_id(check_page(fetched(html)))["js-content"]
        assert c.status == "fail" and "JavaScript" in c.detail

    def test_short_page_without_scripts_is_not_js(self):
        html = page("Tiny", "<p>Just a short page.</p>")
        assert by_id(check_page(fetched(html)))["js-content"].status == "pass"

    def test_visible_word_count_ignores_scripts(self):
        words, scripts = visible_word_count("<body><p>one two</p><script>var a = b c d e</script></body>")
        assert (words, scripts) == (2, 1)

    def test_visible_word_count_empty(self):
        assert visible_word_count("") == (0, 0)


async def test_real_fetch_into_check(web):
    async with make_client(transport=web.transport()) as http:
        res = await fetch(http, "https://acme-labs.com/pricing")
    assert by_id(check_page(res))["noindex"].status == "fail"


async def test_fetch_result_from_redirect_loop_checks(web):
    def loop(request):
        return httpx.Response(302, headers={"location": str(request.url)})
    web.routes["GET https://loop.com/"] = loop
    async with web.client() as http:
        res = await fetch(http, "https://loop.com/")
    assert check_page(res)[0].status == "fail"


class TestPageProblems:
    def test_only_problems_kept(self):
        checks = check_page(fetched(page("x", "<p>short</p>")), "Page /x")  # no canonical -> warn
        out = page_problems(checks, BASE + "/x", "Page /x")
        assert [c.id for c in out] == ["canonical"]

    def test_all_pass_becomes_one_line(self):
        out = page_problems(check_page(fetched(GOOD), "Page /"), BASE + "/", "Page /")
        assert len(out) == 1 and out[0].id == "page-ok" and out[0].label == "Page /: all checks pass"
