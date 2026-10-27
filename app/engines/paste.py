"""Paste mode: analyse an answer copied from ChatGPT, Perplexity, Copilot or any AI app.

This is how the tool covers engines without a free API: the person asks in the normal app,
copies the answer (with its sources), and pastes it here.
"""

from __future__ import annotations

import re

from app.citations import dedupe_citations, make_citation
from app.models import EngineResult

MAX_PASTE_CHARS = 50_000

_MD_LINK = re.compile(r"\[([^\]\n]{0,300})\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"https?://[^\s<>\"'\])}]+")
_TRAILING = ".,;:!?*_"


def parse_pasted(text: str, question: str = "", engine_label: str = "pasted answer") -> EngineResult:
    if text is None or not text.strip():
        raise ValueError("Paste an AI answer that includes its source links")
    if len(text) > MAX_PASTE_CHARS:
        raise ValueError(f"That's too long. Paste at most {MAX_PASTE_CHARS:,} characters")

    found: list[tuple[int, str, str]] = []  # (offset in text, url, title)
    taken: list[tuple[int, int]] = []
    for m in _MD_LINK.finditer(text):
        found.append((m.start(), m.group(2).rstrip(_TRAILING), m.group(1).strip()))
        taken.append(m.span())
    for m in _BARE_URL.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        found.append((m.start(), m.group(0).rstrip(_TRAILING), ""))
    found.sort(key=lambda item: item[0])

    citations = [make_citation(url, title, i + 1) for i, (_, url, title) in enumerate(found)]
    citations = [c for c in citations if "." in c.domain]  # drops junk like "http://x"
    answer = _MD_LINK.sub(r"\1", text)
    answer = _BARE_URL.sub("", answer)
    answer = re.sub(r"[ \t]+", " ", answer).strip()
    return EngineResult(
        engine=engine_label.strip()[:60] or "pasted answer",
        question=question.strip(),
        answer=answer[:4000],
        citations=dedupe_citations(citations),
    )
