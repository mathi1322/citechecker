# Testing

```bash
uv run pytest --cov            # everything except live tests; fails if coverage < 90%
uv run pytest tests/unit       # one layer
uv run pytest -m "not ui"      # skip browser tests
RUN_LIVE=1 uv run --env-file .env pytest -m live   # real Tavily + Groq + Gemini (1 search, ~3 model calls)
```

## No real network in tests

`tests/mockweb.py` is a small fake internet served through `httpx.MockTransport`. It has:
- **acme-labs.com**, the "own" site. robots.txt blocks GPTBot and PerplexityBot; it has a sitemap, a thin,
  outdated labs page, and a noindex pricing page.
- **rival.com**, a strong cited page (direct answer, stats, question headings, fresh date, list, table, sources, quote, schema).
- **g2.com**, a medium page. **reddit.com** returns 403, an unreadable source.
- **Gemini's API**, scripted per question (`web.answer(question, ["rival", "g2"])`, or an HTTP status to fail with),
  including the `vertexaisearch` redirect links real Gemini returns.
- **Tavily and Groq**, scripted per question for the open engine (`web.search(question, ["rival", "own"], cites=[2])`:
  Tavily returns those results and the fake model cites result 2). The fake models also answer memory-check and
  question-suggestion prompts.

The app gets this through `Deps` (`tests/conftest.py::make_deps`), so the code under test is the production code.
DNS is faked too (`_fake_dns` fixture): every host resolves to a public address except `*.internal` names, which
resolve to private ones so the SSRF guard can be tested.
The date is pinned to 2026-10-27 so freshness checks don't drift.

## Layers

| Folder | What | Examples of scenarios |
|---|---|---|
| `tests/unit/` | Each module on its own | open engine: `[n]`/`【n】`/`[1, 3]` citation parsing, out-of-range numbers, Tavily 401/429/432/433/5xx/timeouts/malformed results, Groq model rotation, waiting out per-minute limits, moving on at daily limits, bad key stops at once; memory check mentions/errors/limits; search-rank scoring; engine selection from keys; www/subdomain/lookalike domains; tracking params; redirect resolution success/404/network error; Gemini 429/401/403/400-bad-key/5xx/timeout/retry/malformed JSON/blocked prompt; paste markdown, bare URLs, junk, too long; robots missing/5xx/disallow-all/per-bot/malformed; sitemap index/gzip/soft-404/cap; noindex meta and header; canonical self/relative/elsewhere/missing; JS-only pages; every content feature on rich, thin, empty and non-English pages; date sources and bad dates; gap thresholds and unreadable pages; every fix rule firing and not firing; ordering; cache hit/miss/day rollover; budget; rate-limit window; client IP behind proxies; before/after maths |
| `tests/unit/test_security.py` | SSRF guard and robots.txt | loopback, private, link-local, metadata, IPv6 and IPv4-mapped addresses blocked; redirect into the internal network blocked; sitemap pointing inside ignored; DNS failure; robots.txt disallow for CiteCheckBot or `*`, other bots' rules ignored, missing/empty robots allowed, one robots fetch per site |
| `tests/api/` | HTTP API and pages via FastAPI `TestClient` | validation errors; rate limit per IP; budget exhausted; cached answers don't use budget; report lifecycle; running/error/404 pages; compare 404/409/422; demo report loading and broken demo file; stale reports marked on restart |
| `tests/e2e/` | Whole flows through the public API | report → fix the site (unblock robots, add the missing page, improve content) → report again → compare shows the score up and the fixes resolved; full demo mode without a key |
| `tests/ui/` | Real Chromium via Playwright against a live server | quick check cited / not cited → jump to full report; validation error shown; paste tab; suggest questions → build report → progress → finished report; no horizontal scroll at 375px; dark mode |
| `tests/live/` | Real APIs (opt-in) | open engine returns real results and cites only them; memory check on free Gemini; Gemini grounding (only with `ENGINE=gemini` on a paid project) |

UI tests skip themselves if Chromium isn't installed (`uv run playwright install chromium`).
