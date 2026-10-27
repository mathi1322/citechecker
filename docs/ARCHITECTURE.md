# Architecture

A single FastAPI process with server-rendered pages, a small vanilla-JS file, and SQLite. No build step, no queue,
no external services except the answer-engine APIs (Tavily, Groq, Gemini) and the websites being checked.

## Modules (`app/`)

| Module | Job |
|---|---|
| `main.py` | `create_app()`: pages, JSON API, rate limits, demo-report loading |
| `config.py` | `Settings` from environment variables; `active_engine` picks open / gemini / demo from the keys set; per-engine defaults for runs and budget |
| `models.py` | Pydantic models: `Citation`, `EngineResult`, `Report`, `PageFeatures`, `Fix`… |
| `engines/base.py` | `Engine` protocol, `EngineError` kinds and their user-facing messages |
| `engines/open_engine.py` | The free default engine: `TavilyClient` (search), `GroqClient` (answer writer that rotates models and waits out per-minute limits), `OpenEngine` (maps `[n]` citations back to search results) |
| `engines/gemini.py` | REST call to Gemini with the `google_search` tool; parses `groundingMetadata`; resolves redirect links. Also the plain-text client for the memory check |
| `engines/paste.py` | Pulls cited links out of pasted ChatGPT/Perplexity answers |
| `engines/fake.py` | Deterministic engine for tests and demo mode |
| `citations.py` | Site input validation, URL normalisation, "is this my domain?" (registrable domain via `tldextract`, offline) |
| `fetch.py` | The one HTTP fetch helper: timeouts, size cap, redirect-loop detection, gzip sitemaps; SSRF guard on every request (`check_public`); robots.txt cache for other sites' pages (`RobotsCache`, `polite_fetch`) |
| `questions.py` | Buyer-question suggestions: a free text model (Groq, else Gemini) first, homepage heuristics as a fallback |
| `memory.py` | AI memory check: asks a model without web search and checks whether it names your brand |
| `visibility.py` | `AskService` (cache + budget around an engine), concurrent runs, aggregation into scores |
| `technical.py` | robots.txt per AI crawler, sitemap, status/redirects, noindex, canonical, JavaScript-only content |
| `content.py` | Page features: direct answer, stats, question headings, freshness, lists/tables, sources, quotes, schema |
| `matching.py` | Finds your page that best matches a question (sitemap URL keywords) |
| `gap.py` | Cited pages vs your page, feature by feature |
| `competitors.py` | Third-party site list (reviews, communities, media) and competitor detection |
| `recommend.py` | Rule engine that turns all of the above into ranked fixes |
| `compare.py` | Before/after comparison of two reports |
| `jobs.py` | The report pipeline (`ReportRunner`) and the quick check; dependency wiring (`Deps`: `make_engine`, `make_writer`, `make_memory`) |
| `storage.py` | SQLite: reports, answer cache, daily counters |
| `limits.py` | Per-IP rate limiter, global daily call budget, client IP behind proxies |

## Report pipeline (`jobs.ReportRunner._pipeline`)

1. Fetch homepage, robots.txt and sitemap, then run the technical checks on the homepage.
2. If no questions were given, suggest them (a free text model, or homepage heuristics).
3. Ask every question N times through `AskService`, with concurrency capped by the engine (`max_concurrency`: 1
   for the open engine, whose bottleneck is Groq's per-minute token limit). Each call is cached per question,
   run and UTC day, and counted against the daily budget.
4. Aggregate into the visibility score, per-domain stats, per-question results and search ranks.
5. For up to 5 questions where you weren't cited: find your closest page, fetch the top 3 cited pages
   (skipped in demo mode), extract features, and compare.
6. Run the technical checks on each matched page.
7. AI memory check: up to 5 questions, one at a time, without web search.
8. Generate the ranked fixes.

Progress is saved to SQLite at each step, and the report page polls `/api/reports/{id}`.

## Design decisions

- **REST instead of SDKs** (Gemini, Groq, Tavily): fewer dependencies, and tests mock every API like any other HTTP call.
- **A free open engine as the default**: Google moved search grounding off Gemini's free tier, so the default
  engine combines a free search API with free models. The Gemini engine stays for paid projects.
- **Rules, not an LLM, for fixes**: every fix can be explained, tested, and traced to evidence.
- **Failures are data**: `AskService.ask` never raises. A failed run is stored with its error and left out of
  scores, so one timeout can't sink a 45-call report.
- **Dependency injection via `Deps`**: tests swap in a mock internet (`tests/mockweb.py`) and a fixed date.
- **Background task, not a queue**: fine for one free instance. On restart, unfinished reports are marked failed.
- **Public and no-login, but bounded**: per-IP limits plus a global daily budget keep it within the free tier.
- **Safe fetching**: the server fetches URLs it didn't choose (sitemaps, redirects, cited pages), so every request
  is checked for internal addresses, and other sites' robots.txt is followed. See docs/DEPLOY.md, Security notes.
