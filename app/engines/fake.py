"""A deterministic engine for tests and for running without an API key (demo mode).

Its answers are made up. Reports that use it are labelled "simulated" everywhere in the UI.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from app.citations import dedupe_citations, make_citation
from app.models import EngineResult

# Third-party sites that AI engines often cite, plus placeholder competitors.
SIMULATED_POOL = [
    "https://www.g2.com/categories/simulated",
    "https://www.reddit.com/r/simulated/comments/abc/",
    "https://competitor-one.example/guide",
    "https://competitor-two.example/blog/comparison",
    "https://competitor-three.example/pricing",
    "https://en.wikipedia.org/wiki/Simulated",
    "https://www.forbes.com/simulated-list/",
]

ScriptItem = list[str] | Exception


class FakeEngine:
    name = "fake"
    costly = False

    def __init__(
        self,
        script: dict[str, list[ScriptItem]] | None = None,
        default: list[str] | None = None,
        own_site: str | None = None,
        simulated: bool = True,
        retrieved: dict[str, list[str]] | None = None,
    ):
        """script maps a question to the source URLs (or an exception to raise) for run 1, 2, 3...
        Runs past the end of the list repeat from the start. `retrieved` optionally gives the search
        results a question "read" (like the open engine reports)."""
        self.script = script or {}
        self.retrieved = retrieved or {}
        self.default = default
        self.own_site = own_site
        self.simulated = simulated
        self.calls: dict[str, int] = defaultdict(int)

    async def ask(self, question: str) -> EngineResult:
        run = self.calls[question]
        self.calls[question] += 1
        if question in self.script:
            item = self.script[question][run % len(self.script[question])]
            if isinstance(item, Exception):
                raise item
            urls = item
        elif self.default is not None:
            urls = self.default
        else:
            urls = self._simulate(question, run)
        if question in self.retrieved:
            read = self.retrieved[question]
        elif self.simulated and question not in self.script and self.default is None:
            # Simulated search results: the cited pages plus the rest of the pool, own site included.
            read = urls + [u for u in SIMULATED_POOL if u not in urls]
            if self.own_site and not any(u.startswith(self.own_site) for u in read):
                read.insert(len(urls) + 1, self.own_site.rstrip("/") + "/")
        else:
            read = []
        citations = [make_citation(u, "", i + 1) for i, u in enumerate(urls)]
        return EngineResult(
            engine="simulated (no API key)" if self.simulated else "fake",
            question=question,
            answer=f"Simulated answer for: {question}",
            citations=dedupe_citations(citations),
            simulated=self.simulated,
            retrieved=dedupe_citations([make_citation(u, "", i + 1) for i, u in enumerate(read)]),
        )

    def _simulate(self, question: str, run: int) -> list[str]:
        digest = hashlib.sha256(f"{question}|{run}".encode()).digest()
        count = 3 + digest[0] % 3
        start = digest[1] % len(SIMULATED_POOL)
        urls = [SIMULATED_POOL[(start + i) % len(SIMULATED_POOL)] for i in range(count)]
        if self.own_site and digest[2] % 3 == 0:
            urls.insert(digest[3] % (len(urls) + 1), self.own_site.rstrip("/") + "/")
        return urls
