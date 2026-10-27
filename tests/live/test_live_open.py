"""Calls the real Tavily, Groq and Gemini (free tiers). Off by default.

Run with:  RUN_LIVE=1 uv run --env-file .env pytest -m live
Uses 1 Tavily search credit, ~2 Groq calls and 1 Gemini call.
"""

import os

import pytest

from app.config import Settings
from app.engines.gemini import GeminiClient
from app.engines.open_engine import GroqClient, OpenEngine, TavilyClient
from app.fetch import make_client
from app.memory import memory_check

LIVE = os.environ.get("RUN_LIVE") == "1"
pytestmark = pytest.mark.live


@pytest.mark.skipif(not (LIVE and os.environ.get("TAVILY_API_KEY") and os.environ.get("GROQ_API_KEY")),
                    reason="set TAVILY_API_KEY, GROQ_API_KEY and RUN_LIVE=1")
async def test_open_engine_answers_with_citations():
    async with make_client(30.0) as http:
        engine = OpenEngine(TavilyClient(os.environ["TAVILY_API_KEY"], http),
                            GroqClient(os.environ["GROQ_API_KEY"], Settings.groq_models, http))
        result = await engine.ask("What are the best platforms for hands-on tech labs for enterprise teams?")
    assert len(result.retrieved) >= 5, "Tavily returned too few results"
    assert result.answer
    assert result.citations, f"the model cited nothing: {result.answer[:300]}"
    retrieved_urls = {c.url for c in result.retrieved}
    assert all(c.url in retrieved_urls for c in result.citations)


@pytest.mark.skipif(not (LIVE and os.environ.get("GEMINI_API_KEY")), reason="set GEMINI_API_KEY and RUN_LIVE=1")
async def test_memory_check_on_free_gemini():
    model = os.environ.get("MEMORY_MODEL", Settings.memory_model)
    async with make_client(60.0) as http:
        out = await memory_check(GeminiClient(os.environ["GEMINI_API_KEY"], model, http=http), model,
                                 ["Which companies offer enterprise hands-on tech labs?"], "pluralsight", "pluralsight.com")
    assert out[0].error is None, out[0].error
    assert out[0].answer and out[0].mentioned is not None
