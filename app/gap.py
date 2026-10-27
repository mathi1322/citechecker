"""Compare the pages AI engines cited with the user's own page."""

from __future__ import annotations

from collections.abc import Callable

from app.models import FeatureGap, GapResult, PageFeatures

# (key, label shown to the user, test). Order = display order.
FEATURES: list[tuple[str, str, Callable[[PageFeatures], bool]]] = [
    ("direct_answer", "Answers the question in the first paragraph", lambda f: f.direct_answer),
    ("stats", "Has 3+ specific numbers or stats", lambda f: f.stats_count >= 3),
    ("question_headings", "Uses question-style headings", lambda f: f.question_headings >= 1),
    ("fresh", "Updated in the last 12 months", lambda f: bool(f.fresh)),
    ("lists_tables", "Uses lists or tables", lambda f: f.has_list or f.has_table),
    ("sources", "Links out to 2+ other sources", lambda f: f.outbound_links >= 2),
    ("quotes", "Includes quotes", lambda f: f.quotes >= 1),
    ("depth", "In-depth (800+ words)", lambda f: f.word_count >= 800),
    ("schema", "Has structured data (weak signal)", lambda f: bool(f.schema_types)),
]
FEATURE_LABELS = {key: label for key, label, _ in FEATURES}

# A feature is a "gap" when at least this share of readable winning pages have it, at least
# MIN_WINNERS of them do (one page isn't a pattern), and yours doesn't.
GAP_THRESHOLD = 0.5
MIN_WINNERS = 2


def compare(
    question: str,
    own: PageFeatures | None,
    winners: list[PageFeatures],
    own_is_fallback: bool = False,
) -> GapResult:
    readable = [w for w in winners if w.ok]
    own_ok = own is not None and own.ok
    gaps: list[FeatureGap] = []
    for key, label, test in FEATURES:
        with_it = sum(1 for w in readable if test(w))
        own_has = test(own) if own_ok else None
        is_gap = (
            with_it >= MIN_WINNERS
            and with_it / len(readable) >= GAP_THRESHOLD
            and own_has is False
        )
        gaps.append(FeatureGap(key=key, label=label, winners_with=with_it,
                               winners_total=len(readable), own_has=own_has, is_gap=is_gap))
    return GapResult(
        question=question,
        own_url=own.url if own else None,
        own_is_fallback=own_is_fallback,
        own_features=own,
        winner_features=winners,
        gaps=gaps,
    )
