"""Calls the real Gemini API. Off by default (costs a few requests of your free quota).

Run with:  GEMINI_API_KEY=... RUN_LIVE=1 uv run pytest -m live
"""

import os

import pytest

from app.engines.gemini import GeminiClient, GeminiEngine
from app.fetch import make_client

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not (os.environ.get("GEMINI_API_KEY") and os.environ.get("RUN_LIVE") == "1" and os.environ.get("ENGINE") == "gemini"),
                       reason="Gemini search grounding needs a paid project: set ENGINE=gemini, GEMINI_API_KEY and RUN_LIVE=1"),
]


async def test_grounded_answer_has_real_cited_urls():
    async with make_client(20.0) as http:
        engine = GeminiEngine(GeminiClient(os.environ["GEMINI_API_KEY"], MODEL, http=http), http)
        result = await engine.ask("What are the best platforms for hands-on cloud labs for enterprise teams?")
    assert result.answer
    assert result.citations, "Gemini returned no grounding sources"
    resolved = [c for c in result.citations if "vertexaisearch" not in c.url]
    assert resolved, "redirect links were not resolved to real URLs"
    assert all("." in c.domain for c in result.citations)


async def test_question_suggestions_are_json():
    async with make_client(20.0) as http:
        client = GeminiClient(os.environ["GEMINI_API_KEY"], MODEL, http=http)
        text = await client.generate_text('Return a JSON array with the strings "a" and "b".', json_output=True)
    assert text.strip().startswith("[")
