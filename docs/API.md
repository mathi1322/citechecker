# JSON API

All endpoints take and return JSON. Interactive docs are at `/docs` (FastAPI's built-in Swagger UI).
Errors look like `{"detail": "message"}`. Validation errors (422) may have a list of field errors in `detail`.

| Status | Meaning |
|---|---|
| 422 | Bad input, such as an invalid website, a question that's too long, or too many questions |
| 429 | Per-IP rate limit hit (`REPORTS_PER_HOUR`, `CHECKS_PER_HOUR`) |
| 404 / 409 | Report not found / not finished (compare) |

The **daily AI budget** being used up is *not* an HTTP error. The answer comes back with
`"error": "This demo's daily limit is used up…"`, so the UI can show it next to everything else.

## `POST /api/check` (quick check)

```json
{"site": "example.com", "question": "best hands-on lab platform for enterprises"}
```
Returns an `EngineResult` plus `site`, `domain`, `cited` (bool) and `positions` (where your site appears, 1-based):
```json
{"site": "https://example.com", "domain": "example.com", "cited": false, "positions": [],
 "engine": "gemini (gemini-3.8-flash + Google Search)", "question": "…", "answer": "…",
 "citations": [{"url": "https://…", "title": "pluralsight.com", "domain": "pluralsight.com", "position": 1}],
 "search_queries": ["…"], "error": null, "simulated": false}
```
Uses 1 AI call, or none if the same question was asked today.

## `POST /api/questions`

`{"site": "example.com"}` → `{"questions": [...], "source": "ai" | "heuristic" | "none", "site", "domain"}`.
Uses one free text-model call (Groq, else Gemini) and doesn't count against the daily budget. Without a model, or
if the call fails, it falls back to homepage heuristics.

## `POST /api/reports` (202)

```json
{"site": "example.com", "questions": ["…"], "competitors": ["pluralsight.com"]}
```
`questions` is optional (suggested if empty, max `MAX_QUESTIONS`, duplicates removed). `competitors` is optional (max 10).
Returns `{"id", "url", "status": "queued"}`. The report builds in the background.

## `GET /api/reports/{id}`

The full `Report` (see `app/models.py`): `status` (`queued|running|done|error`), `progress` 0–1, `message`,
`visibility_score`, `questions_cited`, `questions_retrieved` (`null` if the engine doesn't report search results),
`memory[]` (AI memory answers: `question`, `model`, `answer`, `mentioned`, `error`), `domain_stats[]`,
`question_results[]` (runs, top domains, `own_retrieved_rank`, `retrieval_known`, gap analysis),
`technical[]`, `fixes[]`, `competitors[]`, `simulated`, `error`. Poll it until `status` is `done` or `error`.

## `POST /api/paste`

```json
{"site": "example.com", "text": "pasted answer with links", "question": "optional", "engine_label": "ChatGPT"}
```
Same response shape as `/api/check`. Free. Max 50,000 characters.

## `GET /api/compare?a={id}&b={id}`

Both reports must be finished and for the same domain. The order doesn't matter: the older one is "before".
Returns `before_score`, `after_score`, `score_change`, `questions[]` (per-question before/after/change),
`fixed[]` (fix titles that went away), `new_issues[]`, `only_before[]`, `only_after[]`.

## Pages and health

`GET /` home · `GET /reports/{id}` report · `GET /compare?a=&b=` before/after · `GET /demo` example report ·
`GET /healthz` → `{"ok": true, "engine": "open", "demo_mode": false, "budget_remaining": 27}`
