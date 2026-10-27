import json

import httpx
import pytest

from app.engines.base import FRIENDLY_ERRORS, EngineError
from app.engines.gemini import GeminiClient, GeminiEngine, parse_response
from tests.mockweb import GEMINI_URL, REDIRECT, gemini_response


def client_with(handler, **kw) -> tuple[GeminiClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GeminiClient("k", http=http, retry_delay=0, **kw), http


class TestParseResponse:
    def test_normal(self):
        data = gemini_response("Answer text", [("https://a.com", "a.com")], ["q1"])
        assert parse_response(data) == ("Answer text", [("https://a.com", "a.com")], ["q1"])

    def test_multi_part_text_joined(self):
        data = {"candidates": [{"content": {"parts": [{"text": "Hello "}, {"text": "world"}]}}]}
        assert parse_response(data)[0] == "Hello world"

    def test_no_grounding_metadata(self):
        data = {"candidates": [{"content": {"parts": [{"text": "No search used"}]}}]}
        assert parse_response(data) == ("No search used", [], [])

    def test_skips_chunks_without_web_uri(self):
        data = gemini_response("x", [])
        data["candidates"][0]["groundingMetadata"]["groundingChunks"] = [
            {"retrievedContext": {}}, {"web": {"title": "no uri"}}, "junk", {"web": {"uri": "https://ok.com"}}]
        assert parse_response(data)[1] == [("https://ok.com", "")]

    def test_no_candidates(self):
        with pytest.raises(EngineError) as exc:
            parse_response({})
        assert exc.value.kind == "bad_response"

    def test_blocked_prompt(self):
        with pytest.raises(EngineError, match="SAFETY"):
            parse_response({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})

    def test_weird_candidate_shape(self):
        assert parse_response({"candidates": ["oops"]}) == ("", [], [])


class TestGeminiClient:
    def test_requires_key(self):
        with pytest.raises(EngineError) as exc:
            GeminiClient("")
        assert exc.value.kind == "config"

    async def test_sends_key_in_header_not_url(self):
        seen = {}

        def handler(request):
            seen["url"], seen["key"] = str(request.url), request.headers.get("x-goog-api-key")
            return httpx.Response(200, json=gemini_response("ok", []))
        client, http = client_with(handler)
        await client.generate({"contents": []})
        assert seen == {"url": GEMINI_URL, "key": "k"}
        await http.aclose()

    @pytest.mark.parametrize("status, body, kind", [
        (429, {"error": {"message": "Resource exhausted"}}, "quota"),
        (401, {"error": {"message": "nope"}}, "auth"),
        (403, {"error": {"message": "nope"}}, "auth"),
        (400, {"error": {"message": "API key not valid", "details": [{"reason": "API_KEY_INVALID"}]}}, "auth"),
        (400, {"error": {"message": "bad field"}}, "bad_response"),
        (404, "plain text", "bad_response"),
        (500, {}, "server"),
        (503, {}, "server"),
    ])
    async def test_error_mapping(self, status, body, kind):
        def handler(request):
            if isinstance(body, str):
                return httpx.Response(status, text=body)
            return httpx.Response(status, json=body)
        client, http = client_with(handler)
        with pytest.raises(EngineError) as exc:
            await client.generate({})
        assert exc.value.kind == kind
        assert exc.value.friendly == FRIENDLY_ERRORS[kind]
        await http.aclose()

    async def test_retries_once_on_server_error_then_succeeds(self):
        calls = []

        def handler(request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=gemini_response("ok", []))
        client, http = client_with(handler)
        assert (await client.generate({}))["candidates"]
        assert len(calls) == 2
        await http.aclose()

    async def test_timeout_retried_then_raised(self):
        calls = []

        def handler(request):
            calls.append(1)
            raise httpx.ReadTimeout("slow")
        client, http = client_with(handler)
        with pytest.raises(EngineError) as exc:
            await client.generate({})
        assert exc.value.kind == "timeout" and len(calls) == 2
        await http.aclose()

    async def test_quota_not_retried(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(429, json={"error": {"message": "q"}})
        client, http = client_with(handler)
        with pytest.raises(EngineError):
            await client.generate({})
        assert len(calls) == 1
        await http.aclose()

    async def test_network_error_is_server_kind(self):
        def handler(request):
            raise httpx.ConnectError("down")
        client, http = client_with(handler)
        with pytest.raises(EngineError) as exc:
            await client.generate({})
        assert exc.value.kind == "server"
        await http.aclose()

    @pytest.mark.parametrize("content", [b"not json", b"[1, 2]"])
    async def test_malformed_json(self, content):
        client, http = client_with(lambda r: httpx.Response(200, content=content))
        with pytest.raises(EngineError) as exc:
            await client.generate({})
        assert exc.value.kind == "bad_response"
        await http.aclose()

    async def test_generate_text_json_mode(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=gemini_response('["a"]', []))
        client, http = client_with(handler)
        assert await client.generate_text("prompt", json_output=True) == '["a"]'
        assert seen["generationConfig"] == {"responseMimeType": "application/json"}
        assert "tools" not in seen
        await http.aclose()

    def test_error_str_and_friendly_fallback(self):
        assert str(EngineError("quota", "x")) == "quota: x"
        assert str(EngineError("quota")) == "quota"
        assert EngineError("weird", "y").friendly == "weird: y"  # type: ignore[arg-type]


class TestGeminiEngine:
    async def test_ask_resolves_redirects_and_dedupes(self, web):
        web.answer("best labs?", ["rival", "g2", "rival", "https://direct.com/page"])
        async with web.client() as http:
            engine = GeminiEngine(GeminiClient("k", http=http, retry_delay=0), http)
            result = await engine.ask("best labs?")
        assert [c.domain for c in result.citations] == ["rival.com", "g2.com", "direct.com"]
        assert result.citations[0].url == "https://rival.com/guide/best-hands-on-lab-platforms"
        assert [c.position for c in result.citations] == [1, 2, 3]
        assert result.answer == "An answer." and result.ok and not result.simulated
        assert "gemini" in result.engine

    async def test_sends_google_search_tool(self, web):
        async with web.client() as http:
            await GeminiEngine(GeminiClient("k", http=http), http).ask("q")
        body = json.loads(web.requests[0].content)
        assert body["tools"] == [{"google_search": {}}]

    async def test_unresolvable_redirect_falls_back_to_title_domain(self, web):
        web.gemini_answers["q"] = gemini_response("a", [(REDIRECT + "missing", "coursera.org")])
        async with web.client() as http:
            result = await GeminiEngine(GeminiClient("k", http=http), http).ask("q")
        assert result.citations[0].domain == "coursera.org"
