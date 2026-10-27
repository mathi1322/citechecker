"""Measure the content features that AI engines seem to prefer when choosing what to cite.

Every feature is a simple, explainable heuristic (see docs/SCORING.md). None of them is a known
ranking factor. They describe patterns seen in cited pages.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from app.citations import registrable_domain
from app.models import PageFeatures

STOPWORDS = set(
    "a an the and or of for to in on at by with from is are was were be been what which who whom how why "
    "when where do does did can could should would will best top vs versus your you my our we i it its "
    "this that these those about into than then there their they them as if not no yes any some most more "
    "good better tool tools".split()
)
QUESTION_STARTS = ("what", "how", "why", "when", "which", "who", "is ", "are ", "can ", "does ", "do ",
                   "should ", "will ")
FRESH_DAYS = 365

_STAT = re.compile(
    r"(?:[$€£₹]\s?\d[\d,.]*\s?(?:k|m|bn|million|billion)?)"
    r"|(?:\b\d+(?:[.,]\d+)?\s?(?:%|percent\b|x\b|×|million\b|billion\b|k\b|hours?\b|days?\b|weeks?\b|"
    r"months?\b|years?\b|minutes?\b|users\b|customers\b|companies\b|employees\b|learners\b))",
    re.I,
)
_QUOTE = re.compile(r"[\"“][^\"”]{30,400}[\"”]")
_WORD = re.compile(r"\w+", re.U)


def keywords(text: str) -> set[str]:
    """Content words, lightly stemmed ('platforms' -> 'platform')."""
    out = set()
    for w in _WORD.findall(text.lower()):
        if w in STOPWORDS or len(w) < 3 or w.isdigit():
            continue
        out.add(w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w)
    return out


def _main_node(tree: HTMLParser) -> Node | None:
    for selector in ("main", "article", "[role=main]"):
        node = tree.css_first(selector)
        if node is not None:
            return node
    return tree.body


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError, IndexError):
        return None


def _json_ld(tree: HTMLParser) -> list[dict]:
    items: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text() or "")
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                items.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return items


def _schema_types(tree: HTMLParser, ld: list[dict]) -> list[str]:
    types: list[str] = []
    for item in ld:
        t = item.get("@type")
        for value in t if isinstance(t, list) else [t]:
            if isinstance(value, str):
                types.append(value)
    for node in tree.css("[itemtype]"):
        types.append((node.attributes.get("itemtype") or "").rstrip("/").rsplit("/", 1)[-1])
    return sorted({t for t in types if t})


def _modified(tree: HTMLParser, ld: list[dict], headers: dict[str, str]) -> date | None:
    for key in ("dateModified", "datePublished"):
        for item in ld:
            if isinstance(item.get(key), str) and (d := _parse_date(item[key])):
                return d
    for prop in ("article:modified_time", "og:updated_time", "article:published_time"):
        node = tree.css_first(f'meta[property="{prop}"]')
        if node is not None and (d := _parse_date(node.attributes.get("content"))):
            return d
    node = tree.css_first("time[datetime]")
    if node is not None and (d := _parse_date(node.attributes.get("datetime"))):
        return d
    return _parse_date(headers.get("last-modified"))


def is_question_heading(text: str) -> bool:
    t = text.strip().lower()
    return t.endswith("?") or t.startswith(QUESTION_STARTS)


def has_direct_answer(first_paragraph: str, question: str | None) -> bool:
    """A short first paragraph (<= 80 words) that uses the question's key terms."""
    words = _WORD.findall(first_paragraph)
    if not (8 <= len(words) <= 80):
        return False
    if not question:
        return True
    q = keywords(question)
    if not q:
        return True
    return len(q & keywords(first_paragraph)) / len(q) >= 0.3


def extract_features(
    url: str,
    html: str,
    headers: dict[str, str] | None = None,
    question: str | None = None,
    today: date | None = None,
) -> PageFeatures:
    headers = headers or {}
    today = today or date.today()
    tree = HTMLParser(html or "")
    ld = _json_ld(tree)
    schema = _schema_types(tree, ld)
    modified = _modified(tree, ld, headers)
    scripts = len(tree.css("script"))

    for node in tree.css("script, style, noscript, template, svg, nav, footer, header, aside, form"):
        node.decompose()
    main = _main_node(tree)
    if main is None:
        return PageFeatures(url=url, schema_types=schema, modified=modified,
                            fresh=None if modified is None else (today - modified).days <= FRESH_DAYS,
                            js_dependent=scripts > 0)
    text = main.text(separator=" ")
    word_count = len(_WORD.findall(text))

    headings = [h.text(strip=True) for h in main.css("h2, h3")]
    first_par = next((p.text(separator=" ", strip=True) for p in main.css("p")
                      if len(_WORD.findall(p.text())) >= 8), "")

    own = registrable_domain(url)
    outbound = set()
    for a in main.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith(("http://", "https://")):
            dom = registrable_domain(href)
            if dom and dom != own:
                outbound.add(dom)

    return PageFeatures(
        url=url,
        word_count=word_count,
        direct_answer=has_direct_answer(first_par, question),
        stats_count=len(_STAT.findall(text)),
        question_headings=sum(1 for h in headings if is_question_heading(h)),
        headings_count=len(headings),
        has_list=any(len(lst.css("li")) >= 3 for lst in main.css("ul, ol")),
        has_table=main.css_first("table") is not None,
        outbound_links=len(outbound),
        quotes=len(main.css("blockquote")) + len(_QUOTE.findall(text)),
        schema_types=schema,
        modified=modified,
        fresh=None if modified is None else (today - modified).days <= FRESH_DAYS,
        js_dependent=word_count < 120 and scripts > 0,
    )
