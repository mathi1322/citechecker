"""Google Gemini with "Grounding with Google Search": the free engine closest to Google's AI search.

We call the REST API directly with httpx (no SDK) so tests can mock it like any other HTTP call.
Docs: https://ai.google.dev/gemini-api/docs/google-search
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.citations import dedupe_citations, make_citation, resolve_all
from app.engines.base import EngineError
from app.models import EngineResult

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
RETRY_DELAY = 2.0  # seconds before the one retry on timeouts and 5xx errors


class GeminiClient:
    """Thin wrapper over generateContent that turns HTTP failures into EngineError kinds."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        http: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        retry_delay: float | None = None,
    ):
        if not api_key:
            raise EngineError("config", "GEMINI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.http = http or httpx.AsyncClient()
        self.timeout = timeout
        self.retry_delay = RETRY_DELAY if retry_delay is None else retry_delay

    @property
    def url(self) -> str:
        return f"{API_ROOT}/{self.model}:generateContent"

    async def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        # One retry for errors that are usually temporary (timeouts, 5xx).
        for attempt in range(2):
            try:
                return await self._post(payload)
            except EngineError as exc:
                if exc.kind not in {"timeout", "server"} or attempt == 1:
                    raise
                await asyncio.sleep(self.retry_delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self.http.post(
                self.url,
                json=payload,
                headers={"x-goog-api-key": self.api_key},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise EngineError("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise EngineError("server", f"network error: {type(exc).__name__}") from exc

        if resp.status_code == 429:
            raise EngineError("quota", _error_message(resp))
        if resp.status_code in (401, 403) or (
            resp.status_code == 400 and "API_KEY_INVALID" in resp.text
        ):
            raise EngineError("auth", _error_message(resp))
        if resp.status_code >= 500:
            raise EngineError("server", f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise EngineError("bad_response", _error_message(resp))
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise EngineError("bad_response", "response was not JSON") from exc
        if not isinstance(data, dict):
            raise EngineError("bad_response", "response was not a JSON object")
        return data

    async def generate_text(self, prompt: str, json_output: bool = False) -> str:
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if json_output:
            payload["generationConfig"] = {"responseMimeType": "application/json"}
        answer, _, _ = parse_response(await self.generate(payload))
        return answer


def _error_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {resp.status_code}"


def parse_response(data: dict[str, Any]) -> tuple[str, list[tuple[str, str]], list[str]]:
    """Return (answer text, [(source uri, title)], search queries) from a generateContent response."""
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason")
        raise EngineError("bad_response", f"no answer (blocked: {reason})" if reason else "no answer")
    cand = candidates[0] if isinstance(candidates[0], dict) else {}
    parts = (cand.get("content") or {}).get("parts") or []
    answer = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()

    meta = cand.get("groundingMetadata") or {}
    sources: list[tuple[str, str]] = []
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") if isinstance(chunk, dict) else None
        if isinstance(web, dict) and web.get("uri"):
            sources.append((str(web["uri"]), str(web.get("title") or "")))
    queries = [str(q) for q in meta.get("webSearchQueries") or []]
    return answer, sources, queries


class GeminiEngine:
    name = "gemini"
    costly = True
    simulated = False

    def __init__(self, client: GeminiClient, http: httpx.AsyncClient):
        self.client = client
        self.http = http  # used to resolve Google's redirect links to real URLs

    async def ask(self, question: str) -> EngineResult:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": question}]}],
            "tools": [{"google_search": {}}],
        }
        answer, sources, queries = parse_response(await self.client.generate(payload))
        resolved = await resolve_all(self.http, [uri for uri, _ in sources])
        citations = [
            make_citation(url, title, i + 1) for i, (url, (_, title)) in enumerate(zip(resolved, sources))
        ]
        return EngineResult(
            engine=f"gemini ({self.client.model} + Google Search)",
            question=question,
            answer=answer,
            citations=dedupe_citations(citations),
            search_queries=queries,
        )
