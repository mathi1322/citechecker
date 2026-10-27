"""Suggest the questions a buyer would ask an AI assistant, based on what the site says it does."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.engines.base import EngineError
from app.fetch import fetch

if TYPE_CHECKING:
    from app.memory import TextWriter

MAX_QUESTION_CHARS = 300

PROMPT = """You help a company check whether AI assistants recommend it.
Here is what the company's homepage says:

Title: {title}
Description: {description}
Headings: {headings}

Write {n} different questions that a potential buyer, who has never heard of this company, would type
into ChatGPT, Perplexity or Google's AI search while researching this kind of product or service.
Rules: do NOT mention the company or its brand name; mix "best X for Y", comparisons, how-to-choose,
pricing and problem-led questions; keep each under 20 words.
Return only JSON in this shape: {{"questions": ["...", "..."]}}"""


def clean_questions(raw: list[str], limit: int) -> list[str]:
    """Trim, drop empties and duplicates (case-insensitive), cap length and count."""
    out: list[str] = []
    seen: set[str] = set()
    for q in raw:
        if not isinstance(q, str):
            continue
        text = " ".join(q.split())[:MAX_QUESTION_CHARS]
        key = text.lower().rstrip("?")
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _meta(tree: HTMLParser, *keys: str) -> str:
    """First non-empty meta content for any of the keys. Sites write Open Graph tags as
    property="og:…" (the standard) or name="og:…" (common in JavaScript frameworks), so accept both."""
    for key in keys:
        for attr in ("name", "property"):
            node = tree.css_first(f'meta[{attr}="{key}"]')
            content = (node.attributes.get("content") or "").strip() if node is not None else ""
            if content:
                return content
    return ""


def site_summary(html: str) -> dict[str, str]:
    tree = HTMLParser(html or "")
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    headings = [h.text(strip=True) for h in tree.css("h1, h2")][:12]
    return {
        "title": title or _meta(tree, "og:title", "twitter:title"),
        "description": _meta(tree, "description", "og:description", "twitter:description"),
        "headings": " | ".join(h for h in headings if h),
    }


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def mentions_brand(text: str, brand: str) -> bool:
    """'acme-labs' matches 'Acme Labs', 'AcmeLabs' and 'acme labs'."""
    b = _squash(brand)
    return bool(b) and b in _squash(text)


def _topic(summary: dict[str, str], brand: str) -> str:
    """Best guess at the product category, e.g. 'Acme | Enterprise skilling platform' -> 'enterprise skilling platform'."""
    parts = []
    for source in (summary["title"], summary["description"]):
        parts.extend(p.strip() for p in re.split(r"\s[|\-–—:·]\s|[|]", source) if p.strip())
    parts = [p for p in parts if not mentions_brand(p, brand) and 2 <= len(p.split()) <= 8]
    return parts[0].lower().rstrip(".") if parts else ""


def heuristic_questions(summary: dict[str, str], brand: str, n: int) -> list[str]:
    topic = _topic(summary, brand)
    if not topic:
        return []
    templates = [
        "What is the best {t}?",
        "Top {t} options compared",
        "How do I choose {a} {t}?",
        "{T} pricing comparison",
        "Best alternatives for {a} {t}",
        "Which {t} do large companies use?",
        "What should I look for in {a} {t}?",
        "Is {a} {t} worth it for enterprises?",
    ]
    article = "an" if topic[0] in "aeiou" else "a"
    return clean_questions(
        [t.format(t=topic, T=topic[:1].upper() + topic[1:], a=article) for t in templates], n)


async def suggest_questions(
    http: httpx.AsyncClient,
    base_url: str,
    domain: str,
    writer: TextWriter | None = None,
    n: int = 10,
) -> tuple[list[str], str]:
    """Return (questions, source) where source is 'ai', 'heuristic' or 'none'."""
    page = await fetch(http, base_url + "/")
    summary = site_summary(page.text if page.ok else "")
    brand = domain.split(".")[0]
    if writer is not None and (summary["title"] or summary["description"] or summary["headings"]):
        try:
            text = await writer.generate_text(PROMPT.format(n=n, **summary), json_output=True)
            parsed = json.loads(text)
            if isinstance(parsed, dict):  # {"questions": [...]} or any single list inside an object
                parsed = parsed.get("questions") or next((v for v in parsed.values() if isinstance(v, list)), None)
            if isinstance(parsed, list):
                questions = [q for q in clean_questions(parsed, n) if not mentions_brand(q, brand)]
                if questions:
                    return questions, "ai"
        except (EngineError, json.JSONDecodeError, ValueError):
            pass
    questions = heuristic_questions(summary, brand, n)
    return (questions, "heuristic") if questions else ([], "none")
