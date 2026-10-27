"""Find the user's page that best matches a question (the page that *should* have been cited)."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from app.content import keywords

MIN_SCORE = 0.25


def slug_keywords(url: str) -> set[str]:
    path = unquote(urlsplit(url).path).lower()
    return keywords(re.sub(r"[-_/.]+", " ", re.sub(r"\.(html?|php|aspx?)$", "", path)))


def best_page(question: str, urls: list[str], homepage: str) -> tuple[str, float, bool]:
    """Return (url, score 0..1, is_fallback). Scores sitemap URLs by how many of the question's
    keywords appear in the URL path. Falls back to the homepage when nothing matches well."""
    q = keywords(question)
    best_url, best_score = homepage, 0.0
    if q:
        for url in urls:
            words = slug_keywords(url)
            if not words:
                continue
            score = len(q & words) / len(q)
            # Prefer shorter, more focused URLs when scores tie.
            if score > best_score or (score == best_score and score > 0 and len(url) < len(best_url)):
                best_url, best_score = url, score
    if best_score < MIN_SCORE:
        return homepage, best_score, True
    return best_url, best_score, False
