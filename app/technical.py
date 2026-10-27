"""Technical checks: can AI engines read and cite the site at all?

These are the most certain fixes. A blocked crawler or a noindex tag stops a page from being cited,
whatever its content.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.citations import normalize_url
from app.fetch import FetchResult, fetch
from app.models import TechCheck

# user-agent -> (who uses it, does blocking it stop live citations?)
AI_BOTS: dict[str, tuple[str, bool]] = {
    "Googlebot": ("Google Search, AI Overviews and AI Mode", True),
    "Bingbot": ("Bing and Microsoft Copilot (ChatGPT search also uses Bing results)", True),
    "OAI-SearchBot": ("ChatGPT search results", True),
    "ChatGPT-User": ("ChatGPT reading a page when a user asks", True),
    "PerplexityBot": ("Perplexity answers", True),
    "Claude-SearchBot": ("Claude search results", True),
    "GPTBot": ("OpenAI model training", False),
    "Google-Extended": ("Gemini model training (does not affect Google Search or AI Overviews)", False),
    "ClaudeBot": ("Anthropic model training", False),
    "CCBot": ("Common Crawl, used to train many models", False),
}

MIN_TEXT_WORDS = 120


def check_robots(robots: FetchResult, base_url: str) -> tuple[list[TechCheck], list[str]]:
    """Return checks for robots.txt and the sitemap URLs it lists."""
    url = urljoin(base_url + "/", "robots.txt")
    if robots.error and robots.status is None:
        return [TechCheck(id="robots", label="robots.txt", status="warn", url=url,
                          detail=f"Couldn't load robots.txt ({robots.error}). Crawlers may retry later.")], []
    if robots.status is not None and 400 <= robots.status < 500:
        return [TechCheck(id="robots", label="robots.txt", status="pass", url=url,
                          detail="No robots.txt, so every crawler is allowed.")], []
    if robots.status is not None and robots.status >= 500:
        return [TechCheck(id="robots", label="robots.txt", status="fail", url=url,
                          detail=f"robots.txt returns HTTP {robots.status}. Google treats this as "
                                 "'don't crawl anything' until it is fixed.")], []

    parser = RobotFileParser()
    parser.parse(robots.text.splitlines())
    sitemaps = list(parser.site_maps() or [])
    checks: list[TechCheck] = []
    blocked_live, blocked_training = [], []
    for bot, (purpose, live) in AI_BOTS.items():
        if not parser.can_fetch(bot, base_url + "/"):
            (blocked_live if live else blocked_training).append(f"{bot} ({purpose})")
    if blocked_live:
        checks.append(TechCheck(
            id="robots-search-bots", label="AI search crawlers", status="fail", url=url,
            detail="robots.txt blocks crawlers that AI answers rely on: " + "; ".join(blocked_live)))
    else:
        checks.append(TechCheck(
            id="robots-search-bots", label="AI search crawlers", status="pass", url=url,
            detail="Search crawlers (Google, Bing, ChatGPT, Perplexity, Claude) can read the homepage."))
    if blocked_training:
        checks.append(TechCheck(
            id="robots-training-bots", label="AI training crawlers", status="warn", url=url,
            detail="Blocked: " + "; ".join(blocked_training) + ". This can be a deliberate choice. It "
                   "doesn't stop live citations, but the models may know less about you."))
    else:
        checks.append(TechCheck(id="robots-training-bots", label="AI training crawlers", status="pass",
                                url=url, detail="AI training crawlers are allowed."))
    return checks, sitemaps


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Return (page URLs, child sitemap URLs). Tolerant regex parsing: sitemaps are often slightly broken."""
    locs = [m.strip() for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml, flags=re.I)]
    if re.search(r"<sitemapindex", xml, flags=re.I):
        return [], locs
    return locs, []


async def load_sitemap(
    client: httpx.AsyncClient, base_url: str, listed: list[str], max_urls: int = 2000, max_children: int = 5
) -> tuple[TechCheck, list[str]]:
    candidates = listed or [urljoin(base_url + "/", "sitemap.xml")]
    urls: list[str] = []
    found_at = None
    for sm_url in candidates[:3]:
        res = await fetch(client, sm_url)
        if not res.ok or "<loc" not in res.text.lower():
            continue
        found_at = sm_url
        pages, children = parse_sitemap(res.text)
        urls.extend(pages)
        for child in children[:max_children]:
            child_res = await fetch(client, child)
            if child_res.ok:
                urls.extend(parse_sitemap(child_res.text)[0])
            if len(urls) >= max_urls:
                break
        break
    urls = list(dict.fromkeys(urls))[:max_urls]
    if found_at is None:
        return TechCheck(id="sitemap", label="XML sitemap", status="warn",
                         detail="No sitemap found at robots.txt's Sitemap line or /sitemap.xml. "
                                "Sitemaps help crawlers find every page."), []
    return TechCheck(id="sitemap", label="XML sitemap", status="pass", url=found_at,
                     detail=f"Sitemap found with {len(urls)} page URL(s)."), urls


def visible_word_count(html: str) -> tuple[int, int]:
    """(words visible in the raw HTML, number of <script> tags), i.e. what a crawler that doesn't run JavaScript sees."""
    tree = HTMLParser(html or "")
    scripts = len(tree.css("script"))
    for node in tree.css("script, style, noscript, template, svg"):
        node.decompose()
    body = tree.body
    text = body.text(separator=" ") if body is not None else ""
    return len(re.findall(r"\w+", text)), scripts


def check_page(page: FetchResult, label: str = "Page") -> list[TechCheck]:
    """Status, redirects, noindex, canonical and JavaScript-only content for one page."""
    url = page.url
    if page.error == "redirect_loop":
        return [TechCheck(id="status", label=f"{label}: loads", status="fail", url=url,
                          detail="The page redirects in a loop, so no crawler can reach it.")]
    if page.status is None:
        return [TechCheck(id="status", label=f"{label}: loads", status="fail", url=url,
                          detail=f"The page didn't load ({page.error}).")]
    if page.status >= 400:
        return [TechCheck(id="status", label=f"{label}: loads", status="fail", url=url,
                          detail=f"The page returns HTTP {page.status}.")]

    checks = [TechCheck(id="status", label=f"{label}: loads", status="pass", url=url,
                        detail=f"HTTP {page.status}.")]
    if page.redirects > 2:
        checks.append(TechCheck(id="redirects", label=f"{label}: redirects", status="warn", url=url,
                                detail=f"{page.redirects} redirects before the page loads. Link to the final URL."))

    tree = HTMLParser(page.text or "")
    robots_meta = " ".join(
        (n.attributes.get("content") or "").lower()
        for n in tree.css("meta[name]")
        if (n.attributes.get("name") or "").lower() in {"robots", "googlebot", "bingbot"}
    )
    header = page.headers.get("x-robots-tag", "").lower()
    if "noindex" in robots_meta or "noindex" in header:
        where = "an X-Robots-Tag header" if "noindex" in header else "a robots meta tag"
        checks.append(TechCheck(id="noindex", label=f"{label}: indexable", status="fail", url=url,
                                detail=f"The page says 'noindex' in {where}, so search engines and AI "
                                       "answers will leave it out."))
    else:
        checks.append(TechCheck(id="noindex", label=f"{label}: indexable", status="pass", url=url,
                                detail="No noindex found."))

    canonical = next((n.attributes.get("href") for n in tree.css('link[rel="canonical"]')
                      if n.attributes.get("href")), None)
    if not canonical:
        checks.append(TechCheck(id="canonical", label=f"{label}: canonical", status="warn", url=url,
                                detail="No canonical tag. Add one that points to this page's own URL."))
    else:
        absolute = urljoin(page.final_url, canonical)
        if normalize_url(absolute) == normalize_url(page.final_url):
            checks.append(TechCheck(id="canonical", label=f"{label}: canonical", status="pass", url=url,
                                    detail="Canonical points to this page."))
        else:
            checks.append(TechCheck(id="canonical", label=f"{label}: canonical", status="warn", url=url,
                                    detail=f"Canonical points to a different URL ({absolute}). Search "
                                           "engines may index that page instead of this one."))

    words, scripts = visible_word_count(page.text)
    if words < MIN_TEXT_WORDS and scripts >= 1:
        checks.append(TechCheck(id="js-content", label=f"{label}: readable without JavaScript",
                                status="fail", url=url,
                                detail=f"Only {words} words are in the HTML. The rest seems to load with "
                                       "JavaScript, and most AI crawlers don't run JavaScript."))
    else:
        checks.append(TechCheck(id="js-content", label=f"{label}: readable without JavaScript",
                                status="pass", url=url, detail=f"{words} words readable in the raw HTML."))
    return checks


def page_problems(checks: list[TechCheck], url: str, label: str = "Page") -> list[TechCheck]:
    """For extra pages: keep only problems, or one line saying everything passed. Keeps reports short."""
    problems = [c for c in checks if c.status in ("fail", "warn")]
    if problems:
        return problems
    return [TechCheck(id="page-ok", label=f"{label}: all checks pass", status="pass", url=url,
                      detail="Loads, indexable, canonical to itself, readable without JavaScript.")]
