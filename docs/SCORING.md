# How CiteCheck scores and checks

Every number in a report comes from a rule on this page. Where a rule rests on outside research, the source is
linked. Where it's a judgement call, that's said too.

## 1. Visibility

| Metric | Definition |
|---|---|
| **Run** | One answer from the answer engine to one question. The open engine asks each question once (its search results barely change between runs); Gemini asks 3 times, because its answers vary a lot. Set `RUNS_PER_QUESTION` to override. |
| **Cited** | Your registrable domain appears among the answer's sources. `www.`, subdomains and http/https all count as you, so `blog.example.com` counts for `example.com`. Lookalikes like `example.com.evil.io` don't. |
| **Visibility score** (0–100) | For each question: runs that cite you ÷ successful runs. Then the average across questions × 100. Failed runs are left out. |
| **Questions where you're cited** | Questions with at least one run citing you. |
| **Cited in** (per domain) | Successful runs whose sources include the domain at least once ÷ all successful runs. |
| **Share of citations** | That domain's citations ÷ all citations. A domain cited twice in one answer counts twice here, once in "cited in". |
| **Avg. position** | Average place of the domain's first citation in each answer's source list (1 = first). |

### Engines

**Open engine** (default, free). For each question:
1. [Tavily](https://docs.tavily.com) returns the top 10 web results, each with a text snippet.
2. A free Groq model gets the question and the numbered results, with the instruction to answer *only* from
   them and cite the ones it uses as `[n]`.
3. The numbers it cites, in order of first use, become the answer's citations. Numbers that don't match a
   result are ignored.

This is the same search-then-cite design that Perplexity and ChatGPT search use, so it shows *which kinds of pages
win*. It is **not** those products: Tavily's index isn't Google's or Bing's, and the model isn't theirs. Use paste
mode to check the real assistants.

**Gemini** (`ENGINE=gemini`, needs a billing-enabled project). Gemini with
[Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search). Its sources come back as
`vertexaisearch.cloud.google.com` redirect links, which CiteCheck follows (without loading the page) to get the
real URL. If that fails, it falls back to the domain Gemini gives as the title.

### Found by search (open engine)

| Metric | Definition |
|---|---|
| **Found by the AI's search** | Questions where your domain was anywhere in the search results the engine read |
| **Search rank** (per question) | Your best position in those results across runs (1 = top) |

This splits a lost question into two different problems:
- **Not in the results:** the AI never saw you. That's a search-ranking problem: classic SEO.
- **In the results but not cited:** the AI saw you and chose others. That's a content problem, so see the page comparison.

### AI memory

Up to `MEMORY_QUESTIONS` (default 5) questions are asked to a model **without web search** (`gemini-3.5-flash-lite`
on Gemini's free tier, or the first Groq model), with an instruction to name specific companies. An answer
"names you" if it contains your brand (the domain name before the dot, ignoring spaces, hyphens and case:
`acme-labs` matches "Acme Labs") or your domain. Short, common-word brands can produce false matches, so read the
answers shown in the report.

Why it matters: assistants often answer from memory without searching, and a model can only name brands it
learned in training. Blocking AI training crawlers (see below) is one reason it may not know you.

## 2. Technical checks (most certain)

If a crawler can't read a page, the page can't be cited, however good it is.

| Check | Fails / warns when | Priority |
|---|---|---|
| AI search crawlers | robots.txt blocks Googlebot, Bingbot, OAI-SearchBot, ChatGPT-User, PerplexityBot or Claude-SearchBot from the homepage | high |
| AI training crawlers | robots.txt blocks GPTBot, Google-Extended, ClaudeBot or CCBot. This can be deliberate and doesn't stop live citations, so it's only a warning. Google-Extended doesn't affect AI Overviews, which use Googlebot. | low |
| robots.txt | A 5xx response fails (Google treats it as "crawl nothing"). An unreachable file warns. A 404 passes (everything allowed). | medium |
| Loads | Page returns HTTP ≥ 400, times out, or redirects in a loop | high |
| Redirects | More than 2 redirects before the page | medium |
| Indexable | `noindex` in a robots / googlebot / bingbot meta tag or the `X-Robots-Tag` header | high |
| Canonical | Missing (warn), or points to a different URL (warn) | medium |
| Readable without JavaScript | Fewer than 120 words in the raw HTML and at least one `<script>` tag. Most AI crawlers don't run JavaScript. | high |
| XML sitemap | None at robots.txt's `Sitemap:` line or at `/sitemap.xml` | medium |

These run on the homepage and on each page matched to a lost question. For matched pages the report lists only
the problems, or one "all checks pass" line, labelled with the page path.

## 3. Content comparison (patterns, not guarantees)

For up to 5 questions where you weren't cited, CiteCheck fetches the top 3 cited pages (not your own) and your
closest page. **Closest page** means the sitemap URL whose path shares the most keywords with the question,
needing at least 25% of them. If none qualifies, the homepage is used, and that becomes a "create a page" fix.

| Feature | Measured as | Why it's here |
|---|---|---|
| Direct answer | First substantial paragraph is 8–80 words and contains ≥ 30% of the question's keywords | Answer engines pull short, self-contained passages |
| Stats | ≥ 3 numbers with units (%, $, ×, million, users, hours…) | The GEO study found that adding statistics raised visibility in generative answers [1] |
| Question headings | ≥ 1 H2/H3 that ends in "?" or starts with what/how/why/which/is/can… | Matches how people phrase AI questions |
| Fresh | `dateModified` / `article:modified_time` / `<time>` / `Last-Modified` within 365 days | Engines favour current information; an undated page gets no credit |
| Lists or tables | A list with ≥ 3 items, or any table | Easy to extract and compare |
| Sources | Links to ≥ 2 other domains | "Cite sources" was one of the GEO study's strongest methods [1] |
| Quotes | `<blockquote>` or a quoted passage of 30+ characters | "Quotation addition" also helped in the GEO study [1] |
| Depth | ≥ 800 words in the main content (nav, header, footer, aside excluded) | Covers the follow-up questions too |
| Structured data | Any JSON-LD or microdata type. **Labelled a weak signal**: large studies found no measurable citation lift from schema [3]. | Still useful for classic search |

A feature is a **gap** when at least half of the readable cited pages have it, **at least two** of them do (one
page isn't a pattern), and your page doesn't. Unreadable pages (403, timeouts) are left out of the count, and the
report says so.

**Not checked:** llms.txt. A study of about 300k domains found no link between having one and being cited [2],
and Google says it doesn't use it.

## 4. Fixes

Rules in `app/recommend.py`, ordered by priority (high → medium → off-site → low), then by how many questions or
pages each fix affects.

- **Technical**: one fix per failing check, listing the affected pages.
- **Get into the search results first** (high): lost questions where you weren't in the search results at all.
- **The AI saw your page but cited others** (medium): lost questions where you were in the results, with your rank.
- **AI models don't name you from memory** (medium): the memory check never mentioned you.
- **Create pages**: lost questions with no matching page.
- **Content gaps**: one fix per (your page, feature) across all lost questions, with evidence like "5 of 6 cited pages have this".
- **Off-site**: when ≥ 40% of the sources for lost questions are third-party sites: review sites, communities,
  social, media, portals, press releases, research, and any `.edu`/`.gov`-style domain (list in `app/competitors.py`). The advice is to earn real reviews and mentions; never buy them.
- **Study the leader**: when a competitor is cited more often than you. "Competitors" are the domains you listed,
  plus the most-cited domains that aren't you or third-party sites. That's a heuristic, so check the list.
- **Keep it**: when the score is ≥ 80 and nothing is high priority.

## Sources

1. Aggarwal et al., *GEO: Generative Engine Optimization*, KDD 2024, [arXiv:2311.09735](https://arxiv.org/abs/2311.09735)
2. SE Ranking, *LLMs.txt and AI citations* (≈300k domains): <https://seranking.com/blog/llms-txt/>; also
   [Search Engine Journal's summary](https://www.searchenginejournal.com/llms-txt-shows-no-clear-effect-on-ai-citations-based-on-300k-domains/)
3. Search Atlas, *The limits of schema markup for AI search*: <https://searchatlas.com/limits-of-schema-markup-for-ai-search>
