"""CiteCheck's open answer engine: Tavily web search + a free Groq model that writes a cited answer.

This copies how AI search engines like Perplexity and ChatGPT search work: search the web, then have a
model answer from the results and cite the sources it used. It is a stand-in for those engines, not
the same system. Tavily's index isn't Google's or Bing's.

Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search
      https://console.groq.com/docs/rate-limits
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from dataclasses import dataclass

import httpx

from app.citations import dedupe_citations, make_citation
from app.engines.base import EngineError
from app.models import EngineResult

TAVILY_URL = "https://api.tavily.com/search"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq's free plan allows ~8,000 tokens per minute per model. Waiting up to a minute clears that limit;
# a longer retry-after means the daily limit is hit, so try the next model instead.
MAX_RATE_LIMIT_WAIT = 65.0
TRIES_PER_MODEL = 3

ANSWER_PROMPT = """You are an AI search assistant. Answer the question using only the numbered sources below.
Cite sources inline with their numbers, like [1] or [2][5], right after the sentence they support.
Only cite sources you actually use. Recommend specific companies or products when the question asks for them.
Keep it under 200 words.

Question: {question}

Sources:
{sources}"""

_CITE = re.compile(r"[\[【]\s*(\d{1,2}(?:\s*[,，]\s*\d{1,2})*)\s*(?:†[^\]】]*)?[\]】]")


@dataclass
class SearchResult:
    url: str
    title: str
    content: str
    score: float = 0.0


def _status_error(resp: httpx.Response, service: str) -> EngineError:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("error") or body
        if isinstance(detail, dict):
            detail = detail.get("error") or detail.get("message") or json.dumps(detail)
    except ValueError:
        detail = resp.text[:200]
    detail = f"{service} HTTP {resp.status_code}: {str(detail)[:200]}"
    if resp.status_code in (401, 403):
        return EngineError("auth", detail)
    if resp.status_code in (429, 432, 433):  # Tavily uses 432/433 for plan and spending limits
        return EngineError("quota", detail)
    if resp.status_code >= 500:
        return EngineError("server", detail)
    return EngineError("bad_response", detail)


class TavilyClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient, max_results: int = 10, timeout: float = 30.0):
        if not api_key:
            raise EngineError("config", "TAVILY_API_KEY is not set")
        self.api_key = api_key
        self.http = http
        self.max_results = max_results
        self.timeout = timeout

    async def search(self, query: str) -> list[SearchResult]:
        try:
            resp = await self.http.post(
                TAVILY_URL,
                json={"query": query, "max_results": self.max_results, "search_depth": "basic"},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise EngineError("timeout", "Tavily search timed out") from exc
        except httpx.HTTPError as exc:
            raise EngineError("server", f"Tavily network error: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise _status_error(resp, "Tavily")
        try:
            items = resp.json()["results"]
            return [SearchResult(url=str(x["url"]), title=str(x.get("title") or ""),
                                 content=str(x.get("content") or ""), score=float(x.get("score") or 0))
                    for x in items if isinstance(x, dict) and x.get("url")]
        except (ValueError, KeyError, TypeError) as exc:
            raise EngineError("bad_response", "Tavily returned an unexpected response") from exc


class GroqClient:
    """Chat completions on Groq's free models, rotating models and waiting out per-minute limits."""

    def __init__(
        self,
        api_key: str,
        models: tuple[str, ...] | list[str],
        http: httpx.AsyncClient,
        timeout: float = 90.0,
        sleep=asyncio.sleep,
    ):
        if not api_key:
            raise EngineError("config", "GROQ_API_KEY is not set")
        if not models:
            raise EngineError("config", "No Groq models configured")
        self.api_key = api_key
        self.models = list(models)
        self.http = http
        self.timeout = timeout
        self.sleep = sleep
        self._next = itertools.cycle(range(len(self.models)))

    async def _post(self, model: str, prompt: str, json_output: bool) -> httpx.Response:
        body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        if json_output:
            body["response_format"] = {"type": "json_object"}
        try:
            return await self.http.post(GROQ_URL, json=body, timeout=self.timeout,
                                        headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.TimeoutException as exc:
            raise EngineError("timeout", f"Groq {model} timed out") from exc
        except httpx.HTTPError as exc:
            raise EngineError("server", f"Groq network error: {type(exc).__name__}") from exc

    async def generate_text(self, prompt: str, json_output: bool = False) -> str:
        return (await self.generate(prompt, json_output))[0]

    async def generate(self, prompt: str, json_output: bool = False) -> tuple[str, str]:
        """Return (text, model that wrote it)."""
        start = next(self._next)  # spread calls across models so each stays under its limits
        last_error: EngineError | None = None
        for offset in range(len(self.models)):
            model = self.models[(start + offset) % len(self.models)]
            for _ in range(TRIES_PER_MODEL):  # wait out per-minute limits a few times before moving on
                resp = await self._post(model, prompt, json_output)
                if resp.status_code == 200:
                    try:
                        text = resp.json()["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise EngineError("bad_response", f"Groq {model} returned an unexpected response") from exc
                    return text, model
                last_error = _status_error(resp, f"Groq {model}")
                if resp.status_code != 429:
                    if last_error.kind == "auth":
                        raise last_error
                    break  # try the next model
                wait = _retry_after(resp)
                if wait is None or wait > MAX_RATE_LIMIT_WAIT:
                    break  # daily limit: try the next model
                await self.sleep(wait)
        assert last_error is not None
        raise last_error


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def parse_cited_numbers(text: str, count: int) -> list[int]:
    """Source numbers cited in the answer, in order of first use, ignoring numbers that don't exist."""
    order: list[int] = []
    for match in _CITE.finditer(text):
        for raw in re.split(r"[,，]", match.group(1)):
            n = int(raw)
            if 1 <= n <= count and n not in order:
                order.append(n)
    return order


def build_prompt(question: str, results: list[SearchResult]) -> str:
    sources = "\n\n".join(
        f"[{i}] {r.title} ({r.url})\n{r.content[:1500]}" for i, r in enumerate(results, 1)
    )
    return ANSWER_PROMPT.format(question=question, sources=sources)


class OpenEngine:
    name = "open"
    costly = True  # each ask uses one Tavily search credit
    simulated = False
    # One answer at a time: Groq's per-minute token limit is the bottleneck, so running answers in
    # parallel only makes them collide on the limit.
    max_concurrency = 1

    def __init__(self, tavily: TavilyClient, groq: GroqClient):
        self.tavily = tavily
        self.groq = groq

    async def ask(self, question: str) -> EngineResult:
        results = await self.tavily.search(question)
        retrieved = dedupe_citations([make_citation(r.url, r.title, i + 1) for i, r in enumerate(results)])
        if not results:
            return EngineResult(engine=self._label(self.groq.models[0]), question=question,
                                answer="The search returned no results.", retrieved=[])
        answer, model = await self.groq.generate(build_prompt(question, results))
        numbers = parse_cited_numbers(answer, len(results))
        citations = [make_citation(results[n - 1].url, results[n - 1].title, i + 1) for i, n in enumerate(numbers)]
        return EngineResult(
            engine=self._label(model),
            question=question,
            answer=answer.strip(),
            citations=dedupe_citations(citations),
            retrieved=retrieved,
        )

    @staticmethod
    def _label(model: str) -> str:
        return f"open engine (Tavily search + {model})"
