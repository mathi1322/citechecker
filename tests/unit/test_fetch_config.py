import gzip

import httpx
import pytest

from app.config import Settings, load_settings
from app.fetch import MAX_BYTES, USER_AGENT, fetch, make_client


class TestFetch:
    async def test_ok_page(self, web):
        async with web.client() as http:
            r = await fetch(http, "https://acme-labs.com/")
        assert r.ok and r.status == 200 and "Acme Labs" in r.text and r.redirects == 0
        assert web.requests[0].headers["user-agent"] == USER_AGENT

    async def test_404_is_not_ok(self, web):
        async with web.client() as http:
            r = await fetch(http, "https://acme-labs.com/nope")
        assert not r.ok and r.status == 404 and r.error == "http_error"

    async def test_follows_redirects(self, web):
        web.add("https://acme-labs.com/old", "", status=301, headers={"location": "https://acme-labs.com/"})
        async with web.client() as http:
            r = await fetch(http, "https://acme-labs.com/old")
        assert r.ok and r.redirects == 1 and r.final_url == "https://acme-labs.com/"

    async def test_redirect_loop(self, web):
        web.add("https://loop.com/a", "", status=302, headers={"location": "https://loop.com/b"})
        web.add("https://loop.com/b", "", status=302, headers={"location": "https://loop.com/a"})
        async with web.client() as http:
            r = await fetch(http, "https://loop.com/a")
        assert r.error == "redirect_loop" and r.status is None and not r.ok

    @pytest.mark.parametrize("exc, expected", [
        (httpx.ReadTimeout("t"), "timeout"),
        (httpx.ConnectError("c"), "connect: ConnectError"),
    ])
    async def test_network_errors(self, exc, expected):
        def handler(request):
            raise exc
        async with make_client(transport=httpx.MockTransport(handler)) as http:
            r = await fetch(http, "https://x.com")
        assert r.error == expected and r.status is None

    async def test_gzip_body_decoded(self):
        body = gzip.compress(b"<urlset><loc>https://a.com</loc></urlset>")
        async with make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as http:
            r = await fetch(http, "https://a.com/sitemap.xml.gz")
        assert "<loc>https://a.com</loc>" in r.text

    async def test_broken_gzip_gives_empty_text(self):
        body = b"\x1f\x8bnot really gzip"
        async with make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as http:
            r = await fetch(http, "https://a.com/x")
        assert r.text == ""

    async def test_huge_body_truncated(self):
        body = b"a" * (MAX_BYTES + 10)
        async with make_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body))) as http:
            r = await fetch(http, "https://a.com/x")
        assert len(r.text) == MAX_BYTES


class TestSettings:
    @pytest.mark.parametrize("kwargs, engine", [
        ({}, "demo"),
        ({"gemini_api_key": "g"}, "gemini"),
        ({"tavily_api_key": "t"}, "demo"),  # open needs both keys
        ({"tavily_api_key": "t", "groq_api_key": "q"}, "open"),
        ({"tavily_api_key": "t", "groq_api_key": "q", "gemini_api_key": "g"}, "open"),  # open preferred
        ({"tavily_api_key": "t", "groq_api_key": "q", "gemini_api_key": "g", "engine": "gemini"}, "gemini"),
        ({"gemini_api_key": "g", "engine": "open"}, "demo"),
        ({"tavily_api_key": "t", "groq_api_key": "q", "engine": "open"}, "open"),
        ({"engine": "gemini"}, "demo"),
        ({"gemini_api_key": "g", "engine": "demo"}, "demo"),
        ({"gemini_api_key": "g", "force_demo": True}, "demo"),
    ])
    def test_active_engine(self, kwargs, engine):
        s = Settings(**kwargs)
        assert s.active_engine == engine and s.demo_mode == (engine == "demo")

    def test_per_engine_defaults_and_overrides(self):
        open_ = Settings(tavily_api_key="t", groq_api_key="q")
        assert (open_.runs, open_.call_budget) == (1, 30)
        assert (Settings(gemini_api_key="g").runs, Settings(gemini_api_key="g").call_budget) == (3, 450)
        custom = Settings(tavily_api_key="t", groq_api_key="q", runs_per_question=2, daily_call_budget=0)
        assert (custom.runs, custom.call_budget) == (2, 0)

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "  abc ")
        monkeypatch.setenv("TAVILY_API_KEY", "tv")
        monkeypatch.setenv("GROQ_API_KEY", "gq")
        monkeypatch.setenv("GROQ_MODELS", " m1 , m2,, ")
        monkeypatch.setenv("ENGINE", " Gemini ")
        monkeypatch.setenv("RUNS_PER_QUESTION", "5")
        monkeypatch.setenv("DAILY_CALL_BUDGET", "0")
        monkeypatch.setenv("MEMORY_QUESTIONS", "2")
        monkeypatch.setenv("DEMO_MODE", "no")
        s = load_settings()
        assert s.gemini_api_key == "abc" and s.tavily_api_key == "tv" and s.groq_api_key == "gq"
        assert s.groq_models == ("m1", "m2") and s.engine == "gemini" and s.active_engine == "gemini"
        assert s.runs == 5 and s.call_budget == 0 and s.memory_questions == 2 and not s.force_demo

    def test_load_defaults(self, monkeypatch):
        for name in ["GEMINI_API_KEY", "TAVILY_API_KEY", "GROQ_API_KEY", "GROQ_MODELS", "ENGINE",
                     "RUNS_PER_QUESTION", "DAILY_CALL_BUDGET", "DEMO_MODE"]:
            monkeypatch.delenv(name, raising=False)
        s = load_settings()
        assert s.active_engine == "demo" and s.runs == 3 and s.call_budget == 450
        assert s.groq_models == Settings.groq_models and s.runs_per_question is None

    def test_blank_values_use_defaults(self, monkeypatch):
        monkeypatch.setenv("RUNS_PER_QUESTION", "")
        monkeypatch.setenv("DAILY_CALL_BUDGET", "")
        monkeypatch.setenv("ENGINE", "")
        s = load_settings()
        assert s.runs_per_question is None and s.daily_call_budget is None and s.engine == "auto"

    def test_zero_runs_means_engine_default(self, monkeypatch):
        monkeypatch.setenv("RUNS_PER_QUESTION", "0")
        assert load_settings().runs_per_question is None

    def test_bad_integer(self, monkeypatch):
        monkeypatch.setenv("RUNS_PER_QUESTION", "three")
        with pytest.raises(ValueError, match="RUNS_PER_QUESTION"):
            load_settings()

    def test_bad_engine(self, monkeypatch):
        monkeypatch.setenv("ENGINE", "bing")
        with pytest.raises(ValueError, match="ENGINE must be one of"):
            load_settings()
