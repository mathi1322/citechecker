"""A tiny fake internet for tests: a few websites plus a fake Gemini API, served through httpx.MockTransport."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
GEMINI_PREFIX = "https://generativelanguage.googleapis.com/v1beta/models/"
TAVILY_URL = "https://api.tavily.com/search"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
SUGGESTED = ["best hands-on lab platform for enterprises", "how to choose a tech skills platform"]
KEY_URLS = {
    "rival": "https://rival.com/guide/best-hands-on-lab-platforms",
    "g2": "https://www.g2.com/categories/hands-on-labs",
    "reddit": "https://www.reddit.com/r/devops/comments/1/labs/",
    "own": "https://acme-labs.com/labs/hands-on-lab-platform",
}

FILLER = (
    "Teams use the platform to practise real tasks in safe cloud sandboxes, and managers track progress "
    "across every cohort with reports that show who is ready for production work. "
)


def page(title: str, body: str, head: str = "") -> str:
    return f"<!doctype html><html><head><title>{title}</title>{head}</head><body>{body}</body></html>"


def gemini_response(answer: str, sources: list[tuple[str, str]], queries: list[str] | None = None) -> dict:
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": answer}]},
            "finishReason": "STOP",
            "groundingMetadata": {
                "webSearchQueries": queries or ["query"],
                "groundingChunks": [{"web": {"uri": uri, "title": title}} for uri, title in sources],
            },
        }]
    }


def own_home() -> str:
    return page(
        "Acme Labs | Hands-on lab platform for enterprise teams",
        "<header><nav><a href='/'>Home</a></nav></header><main><h1>Hands-on labs for enterprise tech teams</h1>"
        f"<p>{FILLER * 6}</p><h2>Our platform</h2><p>{FILLER * 3}</p></main>",
        head='<meta name="description" content="Hands-on lab platform for enterprise upskilling">'
             '<link rel="canonical" href="https://acme-labs.com/">',
    )


def own_labs_page() -> str:
    # Thin, old page: no stats, no question headings, no sources.
    return page(
        "Hands-on labs | Acme Labs",
        "<main><h1>Hands-on labs</h1><h2>Features</h2>"
        f"<p>{FILLER * 4}</p></main>",
        head='<link rel="canonical" href="https://acme-labs.com/labs/hands-on-lab-platform">'
             '<meta property="article:modified_time" content="2021-03-01T10:00:00Z">',
    )


def rich_competitor_page(today: date) -> str:
    stats = "Teams cut onboarding time by 40% and 2,000 companies use labs, saving $1.2 million a year. "
    quote = "<blockquote>Hands-on practice changed how our engineers learn, said a CTO.</blockquote>"
    table = "<table><tr><th>Platform</th><th>Labs</th></tr><tr><td>A</td><td>500</td></tr></table>"
    lst = "<ul><li>Real cloud sandboxes</li><li>Skill assessments</li><li>Progress reports</li></ul>"
    links = "<a href='https://www.gartner.com/r'>Gartner</a> <a href='https://hbr.org/x'>HBR</a>"
    body = (
        "<article><h1>Best hands-on lab platforms for enterprises</h1>"
        "<p>The best hands-on lab platform for enterprises gives every engineer a real sandbox, "
        "measures skills and reports progress to managers.</p>"
        f"<h2>What is a hands-on lab platform?</h2><p>{stats}{FILLER * 14}</p>{quote}"
        f"<h2>How do you choose one?</h2><p>{FILLER * 14} {links}</p>{lst}{table}</article>"
    )
    ld = json.dumps({"@context": "https://schema.org", "@type": "Article", "dateModified": today.isoformat()})
    return page("Best hands-on lab platforms (2026)", body,
                head=f'<script type="application/ld+json">{ld}</script>')


Route = tuple[int, dict[str, str], str] | Callable[[httpx.Request], httpx.Response]


class MockWeb:
    """Routes are keyed by 'METHOD url' (no query string). Unknown URLs return 404."""

    def __init__(self, today: date | None = None):
        self.today = today or date(2026, 10, 27)
        self.routes: dict[str, Route] = {}
        self.requests: list[httpx.Request] = []
        self.gemini_answers: dict[str, dict | int] = {}  # question -> response JSON, or an HTTP status
        self.search_results: dict[str, list[str] | int] = {}  # Tavily: question -> URLs/keys, or HTTP status
        self.groq_cites: dict[str, list[int] | int] = {}  # Groq: question -> cited source numbers, or status
        self.memory_answer = "From memory I'd suggest Rival and G2 for hands-on labs."
        self.default_site()

    def add(self, url: str, body: str = "", status: int = 200, headers: dict[str, str] | None = None,
            method: str = "GET") -> None:
        self.routes[f"{method} {url}"] = (status, headers or {"content-type": "text/html; charset=utf-8"}, body)

    def default_site(self) -> None:
        self.add("https://acme-labs.com/", own_home())
        self.add("https://acme-labs.com/robots.txt",
                 "User-agent: GPTBot\nDisallow: /\n\nUser-agent: PerplexityBot\nDisallow: /\n\n"
                 "User-agent: *\nAllow: /\n\nSitemap: https://acme-labs.com/sitemap.xml\n",
                 headers={"content-type": "text/plain"})
        self.add("https://acme-labs.com/sitemap.xml",
                 "<?xml version='1.0'?><urlset>"
                 "<url><loc>https://acme-labs.com/</loc></url>"
                 "<url><loc>https://acme-labs.com/labs/hands-on-lab-platform</loc></url>"
                 "<url><loc>https://acme-labs.com/pricing</loc></url></urlset>",
                 headers={"content-type": "application/xml"})
        self.add("https://acme-labs.com/labs/hands-on-lab-platform", own_labs_page())
        self.add("https://acme-labs.com/pricing", page("Pricing", f"<main><p>{FILLER * 8}</p></main>",
                                                      head='<meta name="robots" content="noindex">'))
        self.add("https://rival.com/guide/best-hands-on-lab-platforms", rich_competitor_page(self.today))
        self.add("https://www.g2.com/categories/hands-on-labs",
                 page("Best labs software", f"<main><h2>What are hands-on labs?</h2><p>{FILLER * 30} 4.5 "
                      "percent of users, 300 reviews and 12 hours saved, 50 customers.</p>"
                      "<ul><li>a</li><li>b</li><li>c</li></ul></main>",
                      head=f'<meta property="article:modified_time" content="{self.today.isoformat()}">'))
        self.add("https://www.reddit.com/r/devops/comments/1/labs/", "blocked", status=403)
        for key, target in {
            "rival": "https://rival.com/guide/best-hands-on-lab-platforms",
            "g2": "https://www.g2.com/categories/hands-on-labs",
            "reddit": "https://www.reddit.com/r/devops/comments/1/labs/",
            "own": "https://acme-labs.com/labs/hands-on-lab-platform",
        }.items():
            self.add(REDIRECT + key, "", status=302, headers={"location": target})

    def answer(self, question: str, sources: list[str] | int, text: str = "An answer.") -> None:
        """Script the fake Gemini: sources are redirect keys ('rival', 'g2', 'reddit', 'own') or full URLs,
        or an HTTP status code to fail with."""
        if isinstance(sources, int):
            self.gemini_answers[question] = sources
            return
        chunks = []
        titles = {"rival": "rival.com", "g2": "g2.com", "reddit": "reddit.com", "own": "acme-labs.com"}
        for s in sources:
            chunks.append((REDIRECT + s, titles[s]) if s in titles else (s, ""))
        self.gemini_answers[question] = gemini_response(text, chunks)

    def search(self, question: str, sources: list[str] | int, cites: list[int] | int | None = None) -> None:
        """Script the open engine: Tavily returns `sources` (keys like 'rival' or URLs, or an HTTP status)
        and Groq cites the given source numbers (default [1])."""
        self.search_results[question] = sources
        if cites is not None:
            self.groq_cites[question] = cites

    def _plain_text(self, prompt: str) -> str:
        """What a model without web search says: a memory answer, or suggested questions as JSON."""
        if "without searching the web" in prompt:
            return self.memory_answer
        return json.dumps({"questions": SUGGESTED})

    def _tavily(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        scripted = self.search_results.get(query, ["g2"])
        if isinstance(scripted, int):
            return httpx.Response(scripted, json={"detail": {"error": "scripted failure"}})
        results = [{"url": KEY_URLS.get(k, k), "title": f"Title {k}", "content": f"About {k}.", "score": 0.9 - i / 20}
                   for i, k in enumerate(scripted)]
        return httpx.Response(200, json={"query": query, "results": results, "response_time": 0.1})

    def _groq(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        if "Sources:" in prompt:
            question = re.search(r"Question: (.*)", prompt).group(1).strip()
            cites = self.groq_cites.get(question, [1])
            if isinstance(cites, int):
                return httpx.Response(cites, json={"error": {"message": "scripted failure"}})
            text = "Answer. " + " ".join(f"Point [{n}]." for n in cites)
        else:
            text = self._plain_text(prompt)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}}]})

    def _gemini(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["contents"][0]["parts"][0]["text"]
        if "tools" not in body:  # no web search: question suggestions or the memory check
            return httpx.Response(200, json=gemini_response(self._plain_text(prompt), []))
        scripted = self.gemini_answers.get(prompt)
        if scripted is None:
            scripted = gemini_response("Generic answer.", [(REDIRECT + "g2", "g2.com")])
        if isinstance(scripted, int):
            return httpx.Response(scripted, json={"error": {"code": scripted, "message": "scripted failure"}})
        return httpx.Response(200, json=scripted)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url.copy_with(query=None))
        if request.method == "POST" and url.startswith(GEMINI_PREFIX) and url.endswith(":generateContent"):
            return self._gemini(request)
        if request.method == "POST" and url == TAVILY_URL:
            return self._tavily(request)
        if request.method == "POST" and url == GROQ_URL:
            return self._groq(request)
        route = self.routes.get(f"{request.method} {url}")
        if route is None:
            return httpx.Response(404, text="not found")
        if callable(route):
            return route(request)
        status, headers, body = route
        return httpx.Response(status, headers=headers, text=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        from app.fetch import make_client
        return make_client(5.0, self.transport())
