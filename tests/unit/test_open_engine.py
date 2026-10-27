import json

import httpx
import pytest

from app.engines.base import EngineError
from app.engines.open_engine import (
    GROQ_URL,
    TAVILY_URL,
    GroqClient,
    OpenEngine,
    SearchResult,
    TavilyClient,
    build_prompt,
    parse_cited_numbers,
)
from tests.mockweb import KEY_URLS


def http_with(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def groq_ok(text="hello"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


class TestParseCitedNumbers:
    @pytest.mark.parametrize("text, count, expected", [
        ("A [1]. B [2][5].", 5, [1, 2, 5]),
        ("A [3]. B [1]. C [3].", 5, [3, 1]),  # order of first use, no repeats
        ("A [1, 3] and [2，4].", 5, [1, 3, 2, 4]),
        ("gpt-oss style 【2†L1-L5】 and 【4】", 5, [2, 4]),
        ("Out of range [0] [7] [2]", 5, [2]),
        ("No citations at all.", 5, []),
        ("Years like [2026] aren't sources", 10, []),
        ("[ 1 ] spaced", 2, [1]),
    ])
    def test_parse(self, text, count, expected):
        assert parse_cited_numbers(text, count) == expected


def test_build_prompt_numbers_and_truncates():
    prompt = build_prompt("q?", [SearchResult("https://a.com", "A", "x" * 3000), SearchResult("https://b.com", "B", "b")])
    assert "Question: q?" in prompt and "[1] A (https://a.com)" in prompt and "[2] B (https://b.com)" in prompt
    assert "x" * 1500 in prompt and "x" * 1501 not in prompt


class TestTavily:
    async def test_parses_results_and_sends_auth(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"results": [
                {"url": "https://a.com", "title": "A", "content": "aa", "score": 0.9},
                {"title": "no url"}, "junk", {"url": "https://b.com"}]})
        async with http_with(handler) as http:
            results = await TavilyClient("key", http).search("best labs")
        assert [r.url for r in results] == ["https://a.com", "https://b.com"]
        assert results[0].score == 0.9 and results[1].title == "" and results[1].score == 0
        assert seen["auth"] == "Bearer key"
        assert seen["body"] == {"query": "best labs", "max_results": 10, "search_depth": "basic"}

    def test_requires_key(self):
        with pytest.raises(EngineError) as exc:
            TavilyClient("", httpx.AsyncClient())
        assert exc.value.kind == "config"

    @pytest.mark.parametrize("status, kind", [(401, "auth"), (403, "auth"), (429, "quota"), (432, "quota"),
                                              (433, "quota"), (500, "server"), (400, "bad_response")])
    async def test_status_errors(self, status, kind):
        async with http_with(lambda r: httpx.Response(status, json={"detail": {"error": "nope"}})) as http:
            with pytest.raises(EngineError) as exc:
                await TavilyClient("k", http).search("q")
        assert exc.value.kind == kind and "Tavily HTTP" in str(exc.value)

    async def test_non_json_error_body(self):
        async with http_with(lambda r: httpx.Response(502, text="bad gateway")) as http:
            with pytest.raises(EngineError, match="bad gateway"):
                await TavilyClient("k", http).search("q")

    @pytest.mark.parametrize("exc, kind", [(httpx.ReadTimeout("t"), "timeout"), (httpx.ConnectError("c"), "server")])
    async def test_network_errors(self, exc, kind):
        def handler(request):
            raise exc
        async with http_with(handler) as http:
            with pytest.raises(EngineError) as err:
                await TavilyClient("k", http).search("q")
        assert err.value.kind == kind

    @pytest.mark.parametrize("response", [httpx.Response(200, text="nope"), httpx.Response(200, json={"x": 1}),
                                          httpx.Response(200, json={"results": [{"url": "u", "score": "high"}]})])
    async def test_malformed(self, response):
        async with http_with(lambda r: response) as http:
            with pytest.raises(EngineError) as exc:
                await TavilyClient("k", http).search("q")
        assert exc.value.kind == "bad_response"


class TestGroq:
    def make(self, handler, models=("m1", "m2")):
        waits = []

        async def fake_sleep(seconds):
            waits.append(seconds)
        http = http_with(handler)
        return GroqClient("key", models, http, sleep=fake_sleep), waits, http

    @pytest.mark.parametrize("key, models", [("", ("m",)), ("k", ())])
    def test_config_errors(self, key, models):
        with pytest.raises(EngineError) as exc:
            GroqClient(key, models, httpx.AsyncClient())
        assert exc.value.kind == "config"

    async def test_rotates_models_between_calls(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content)["model"])
            return groq_ok()
        client, _, http = self.make(handler)
        assert [await client.generate("p") for _ in range(3)] == [("hello", "m1"), ("hello", "m2"), ("hello", "m1")]
        assert seen == ["m1", "m2", "m1"]
        await http.aclose()

    async def test_short_rate_limit_waits_and_retries_same_model(self):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content)["model"])
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "TPM"}})
            return groq_ok("after wait")
        client, waits, http = self.make(handler)
        assert await client.generate("p") == ("after wait", "m1")
        assert calls == ["m1", "m1"] and waits == [7.0]
        await http.aclose()

    @pytest.mark.parametrize("headers", [{"retry-after": "3600"}, {}, {"retry-after": "soon"}])
    async def test_daily_limit_moves_to_next_model(self, headers):
        def handler(request):
            if json.loads(request.content)["model"] == "m1":
                return httpx.Response(429, headers=headers, json={"error": {"message": "TPD"}})
            return groq_ok("from m2")
        client, waits, http = self.make(handler)
        assert await client.generate("p") == ("from m2", "m2") and waits == []
        await http.aclose()

    async def test_all_models_limited_raises_quota(self):
        client, _, http = self.make(lambda r: httpx.Response(429, json={"error": {"message": "TPD"}}))
        with pytest.raises(EngineError) as exc:
            await client.generate("p")
        assert exc.value.kind == "quota" and "Groq m2" in str(exc.value)
        await http.aclose()

    async def test_repeated_short_limit_gives_up_after_one_wait_per_model(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429, headers={"retry-after": "1"}, json={"error": {"message": "TPM"}})
        client, waits, http = self.make(handler)
        with pytest.raises(EngineError):
            await client.generate("p")
        assert len(calls) == 6 and len(waits) == 6  # 3 tries x 2 models
        await http.aclose()

    async def test_bad_key_stops_immediately(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        client, _, http = self.make(handler)
        with pytest.raises(EngineError) as exc:
            await client.generate("p")
        assert exc.value.kind == "auth" and len(calls) == 1
        await http.aclose()

    async def test_model_error_tries_next_model(self):
        def handler(request):
            if json.loads(request.content)["model"] == "m1":
                return httpx.Response(500, json={"error": {"message": "Internal Server Error"}})
            return groq_ok()
        client, _, http = self.make(handler)
        assert (await client.generate("p"))[1] == "m2"
        await http.aclose()

    @pytest.mark.parametrize("response", [httpx.Response(200, text="x"), httpx.Response(200, json={"choices": []})])
    async def test_malformed_success(self, response):
        client, _, http = self.make(lambda r: response)
        with pytest.raises(EngineError) as exc:
            await client.generate("p")
        assert exc.value.kind == "bad_response"
        await http.aclose()

    async def test_null_content_is_empty_text(self):
        client, _, http = self.make(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
        assert await client.generate_text("p") == ""
        await http.aclose()

    @pytest.mark.parametrize("exc, kind", [(httpx.ReadTimeout("t"), "timeout"), (httpx.ConnectError("c"), "server")])
    async def test_network_errors(self, exc, kind):
        def handler(request):
            raise exc
        client, _, http = self.make(handler)
        with pytest.raises(EngineError) as err:
            await client.generate("p")
        assert err.value.kind == kind
        await http.aclose()

    async def test_json_mode_and_auth_header(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            seen["auth"] = request.headers["authorization"]
            seen["url"] = str(request.url)
            return groq_ok("{}")
        client, _, http = self.make(handler)
        await client.generate_text("p", json_output=True)
        assert seen["response_format"] == {"type": "json_object"} and seen["auth"] == "Bearer key"
        assert seen["url"] == GROQ_URL
        await http.aclose()


class TestOpenEngine:
    def engine(self, http):
        return OpenEngine(TavilyClient("t", http), GroqClient("g", ("m1", "m2"), http))

    async def test_ask_maps_cited_numbers_to_urls(self, web):
        web.search("best labs", ["rival", "g2", "own", "reddit"], cites=[3, 1])
        async with web.client() as http:
            result = await self.engine(http).ask("best labs")
        assert [c.url for c in result.citations] == [KEY_URLS["own"], KEY_URLS["rival"]]
        assert [c.position for c in result.citations] == [1, 2]
        assert [c.domain for c in result.retrieved] == ["rival.com", "g2.com", "acme-labs.com", "reddit.com"]
        assert result.engine == "open engine (Tavily search + m1)" and result.answer.startswith("Answer.")
        assert result.ok and not result.simulated

    async def test_no_citations_but_retrieved(self, web):
        web.search("q", ["rival", "g2"], cites=[])
        async with web.client() as http:
            result = await self.engine(http).ask("q")
        assert result.citations == [] and len(result.retrieved) == 2

    async def test_no_search_results_skips_the_model(self, web):
        web.search("q", [])
        async with web.client() as http:
            result = await self.engine(http).ask("q")
        assert result.citations == [] and result.retrieved == [] and "no results" in result.answer
        assert not any(str(r.url) == GROQ_URL for r in web.requests)

    async def test_search_failure_raises(self, web):
        web.search("q", 432)
        async with web.client() as http:
            with pytest.raises(EngineError) as exc:
                await self.engine(http).ask("q")
        assert exc.value.kind == "quota"
        assert all(str(r.url) == TAVILY_URL for r in web.requests)

    async def test_model_failure_raises(self, web):
        web.search("q", ["rival"], cites=401)
        async with web.client() as http:
            with pytest.raises(EngineError) as exc:
                await self.engine(http).ask("q")
        assert exc.value.kind == "auth"
