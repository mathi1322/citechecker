# CiteCheck

**Is your website cited when buyers ask AI?** CiteCheck asks an AI search engine the questions your buyers ask,
records which websites it cites, checks whether AI crawlers can read your site, compares your pages with the
pages that got cited, and gives you a ranked list of fixes. Run it again after making changes to see what moved.

It works for any website. No login is needed.

## What you can do with it

| Feature | What it does |
|---|---|
| **Quick check** | One question, one answer: is your site among the cited sources? About 10 seconds. |
| **Full report** | 10–20 buyer questions (written for you from your homepage, or your own), each asked 3 times. You get a visibility score, who gets cited instead, technical checks, page comparisons and ranked fixes. |
| **Paste mode** | Paste an answer from ChatGPT, Perplexity, Copilot or Claude (free apps) and see who it cites. |
| **Before / after** | Compare two reports for the same site to see whether your changes worked. |

## How it works

```
your site + buyer questions
        │
        ▼
 AI answer engine ──► answer + cited sources, and the search results it read
        │
        ├─► visibility score, share of voice per domain
        ├─► found by search? (were you in the results at all, or seen but not chosen?)
        ├─► AI memory: does a model name you with no web search?
        ├─► technical checks: robots.txt AI crawlers, noindex, canonical, JS-only pages, sitemap
        ├─► page comparison: cited pages vs your closest page (direct answer, stats, headings, freshness…)
        └─► ranked fixes: high / medium / off-site / low, each with its evidence
```

### Answer engines

CiteCheck picks the engine from the keys you set (`ENGINE=auto`):

| Engine | Keys | Cost | What it is |
|---|---|---|---|
| **open** (default when its keys are set) | `TAVILY_API_KEY` + `GROQ_API_KEY` | Free tiers: Tavily 1,000 searches/month, Groq free models | Tavily web search, then a free Groq model writes an answer citing the results it used. The same search-then-cite design as Perplexity and ChatGPT search, but **not** those products. |
| **gemini** | `GEMINI_API_KEY` on a billing-enabled project | Paid tier (Google lists a monthly free allowance of grounded requests) | Gemini with Grounding with Google Search |
| **demo** | none | Free | Simulated answers, clearly labelled. Site checks are still real. |

The **AI memory** check uses `GEMINI_API_KEY` on the free tier (`gemini-3.5-flash-lite`), or Groq if there's no Gemini key.
For the real ChatGPT and Perplexity, use **paste mode**.

How each score and check is calculated, the research behind it, and its limits: [docs/SCORING.md](docs/SCORING.md).

## Run it locally

Needs [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for you.

```bash
uv sync
cp .env.example .env              # then add your free keys (see the table above)
uv run --env-file .env uvicorn app.main:create_app --factory --reload
# open http://127.0.0.1:8000
```

Without keys the app runs in **demo mode**. AI answers are simulated and clearly labelled; the site checks are still real.

## Tests

```bash
uv run pytest --cov                     # unit + API + end-to-end + browser tests, coverage must be ≥ 90%
uv run playwright install chromium      # once, for the browser tests
RUN_LIVE=1 uv run --env-file .env pytest -m live   # optional: real Tavily/Groq/Gemini calls
```

No normal test calls a real AI API or the real internet. See [docs/TESTING.md](docs/TESTING.md).

## Deploy

Free on Render (Docker, `render.yaml` included). See [docs/DEPLOY.md](docs/DEPLOY.md).

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data flow, design decisions
- [docs/SCORING.md](docs/SCORING.md): every metric and check, why it's there, and its limits
- [docs/API.md](docs/API.md): JSON API reference
- [docs/TESTING.md](docs/TESTING.md): what's tested and how
- [docs/DEPLOY.md](docs/DEPLOY.md): Render, Hugging Face, keeping it inside the free tier
- [LOG.md](LOG.md): build log

## Limits, honestly

- The open engine is a stand-in for AI search, not ChatGPT or Google: Tavily's index isn't Google's or Bing's. It
  shows the same mechanism (search, then choose what to cite) and which pages win. Check the real assistants with paste mode.
- AI answers change between runs and over time. Treat small score changes as noise.
- The content checks show patterns in pages that got cited. They are not known ranking factors. The technical
  checks are the sure ones: a page that crawlers can't read can't be cited.
