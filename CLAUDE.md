# CiteCheck: notes for Claude

CiteCheck checks whether AI answer engines cite a website for the questions its buyers ask, who they cite
instead, and what to fix. It's a general tool for any site, deployed publicly with no login.

If `CLAUDE.local.md` exists (git-ignored, the owner's private notes), read it too.

- `LOG.md` is a template that **the owner writes in their own words**. Never fill it in for them; offer facts they
  can draw from instead.
- Present findings carefully: the default engine is a stand-in for AI search (see below), not ChatGPT or Google.

## Commands

```bash
uv run uvicorn app.main:create_app --factory --reload --env-file .env   # http://127.0.0.1:8000
uv run pytest --cov                                                      # must stay >= 90% (currently ~99.7%)
uv run playwright install chromium                                       # once, for tests/ui
RUN_LIVE=1 uv run --env-file .env pytest -m live                         # real APIs, uses 1 Tavily credit
uv run --env-file .env python scripts/build_demo.py freecodecamp.org     # rebuild app/demo/demo_report.json (~10 credits)
```

`.env` holds TAVILY_API_KEY, GROQ_API_KEY and GEMINI_API_KEY and is git-ignored. Never print or commit it; check
whether a key is set with `grep -qE '^KEY=.+' .env`.

## Engines (decided after testing the free tiers in Oct 2026)

- **open** (default): Tavily search (free, 1,000/month) → a free Groq model (`openai/gpt-oss-120b`,
  `qwen/qwen3.8-27b`, rotating) writes an answer citing `[n]`. 1 run per question; answers run one at a time
  (`max_concurrency = 1`) because Groq's ~8k tokens/minute per model is the bottleneck.
- **gemini**: Gemini + Google Search grounding. **Doesn't work on the free tier** (always 429); needs billing.
  `gemini-2.5-*` is closed to new users; default is `gemini-3.8-flash`.
- **Memory check**: `gemini-3.5-flash-lite` on the free tier (no search) asks whether the model names the brand.
- Groq's built-in browser search and `groq/compound` aren't usable free (one search answer used ~190k of 200k daily tokens).

## Code map

See `docs/ARCHITECTURE.md`. Key points: `app/jobs.py` is the pipeline and wiring (`Deps`); `app/recommend.py` is a
plain rule engine (no LLM) so every fix is explainable; `app/fetch.py` has the SSRF guard and robots.txt cache;
every scoring rule is documented in `docs/SCORING.md`. Keep docs in sync when rules change.

Tests use a fake internet (`tests/mockweb.py`) with fake Gemini/Tavily/Groq, fake DNS, and a fixed date
(2026-10-27). No regular test may touch the real network. Browser tests run last (Playwright's loop breaks
async tests that follow it).

## Hosting

Render free tier via `render.yaml` (Hugging Face Docker Spaces now need PRO); every push to `main` redeploys.
Keep it awake with an UptimeRobot ping on `/healthz`. The example report is freecodecamp.org, chosen as a neutral,
well-known site.
