"""AI memory check: does a model name your brand from what it learned in training, with no web search?

AI assistants often answer from memory, without searching. A brand they've never learned about
won't come up there, however well its pages rank. Blocking AI training crawlers (GPTBot,
Google-Extended, CCBot…) in robots.txt is one reason a model may not know you.
"""

from __future__ import annotations

from typing import Protocol

from app.engines.base import EngineError
from app.models import MemoryResult
from app.questions import mentions_brand

PROMPT = """{question}

Answer from your own knowledge, without searching the web. Name the specific companies, products or
websites you would recommend, most relevant first. Keep it under 150 words."""


class TextWriter(Protocol):
    async def generate_text(self, prompt: str, json_output: bool = False) -> str: ...


async def memory_check(
    writer: TextWriter, model_label: str, questions: list[str], brand: str, domain: str, limit: int = 5
) -> list[MemoryResult]:
    """Ask up to `limit` questions one at a time (free tiers allow few calls per minute)."""
    results: list[MemoryResult] = []
    for question in questions[:limit]:
        try:
            answer = await writer.generate_text(PROMPT.format(question=question))
        except EngineError as exc:
            results.append(MemoryResult(question=question, model=model_label, error=exc.friendly))
            continue
        mentioned = mentions_brand(answer, brand) or domain.lower() in answer.lower()
        results.append(MemoryResult(question=question, model=model_label, answer=answer.strip()[:2000],
                                    mentioned=mentioned))
    return results
