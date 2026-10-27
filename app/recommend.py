"""Turn measurements into a ranked list of fixes. Plain rules, no AI, so every fix is explainable and testable."""

from __future__ import annotations

from collections import Counter, defaultdict

from app.competitors import third_party_kind
from app.gap import FEATURE_LABELS
from app.models import DomainStat, Fix, MemoryResult, QuestionResult, TechCheck

PRIORITY_ORDER = {"high": 0, "medium": 1, "off-site": 2, "low": 3}

# How to fix each content gap, in plain words.
FEATURE_ADVICE = {
    "direct_answer": "Start the page with a 2-3 sentence answer to the question before anything else.",
    "stats": "Add specific numbers: results, prices, time saved, customer counts, with dates and sources.",
    "question_headings": "Use the questions buyers ask as H2/H3 headings, each followed by a direct answer.",
    "fresh": "Update the page and show the date (visible 'Updated on' and dateModified).",
    "lists_tables": "Put comparisons, steps and feature lists into lists or a table.",
    "sources": "Back up claims with links to credible outside sources (research, standards, reports).",
    "quotes": "Add a quote from an expert or customer, with their name and role.",
    "depth": "Cover the topic in more depth: use cases, how it works, pricing, FAQs.",
    "schema": "Add matching structured data (Article, FAQPage, Product). This is a weak signal for AI answers.",
}
# Technical check id -> priority when it fails / warns.
TECH_PRIORITY = {
    "robots-search-bots": "high", "noindex": "high", "status": "high", "js-content": "high",
    "canonical": "medium", "redirects": "medium", "sitemap": "medium", "robots": "medium",
    "robots-training-bots": "low",
}
TECH_TITLES = {
    "robots-search-bots": "Let AI search crawlers read your site",
    "noindex": "Remove noindex from pages you want cited",
    "status": "Fix pages that don't load",
    "js-content": "Make content readable without JavaScript",
    "canonical": "Fix canonical tags",
    "redirects": "Shorten redirect chains",
    "sitemap": "Add an XML sitemap",
    "robots": "Fix robots.txt",
    "robots-training-bots": "Review the AI training crawlers you block",
}
OFF_SITE_SHARE = 0.4


def technical_fixes(checks: list[TechCheck]) -> list[Fix]:
    grouped: dict[str, list[TechCheck]] = defaultdict(list)
    for c in checks:
        if c.status in ("fail", "warn"):
            grouped[c.id].append(c)
    fixes = []
    for check_id, items in grouped.items():
        first = items[0]
        priority = TECH_PRIORITY.get(check_id, "medium")
        if first.status == "warn" and priority == "high":
            priority = "medium"
        pages = [c.url for c in items if c.url]
        fixes.append(Fix(
            rule_id=f"tech:{check_id}",
            priority=priority,  # type: ignore[arg-type]
            title=TECH_TITLES.get(check_id, first.label),
            detail=first.detail,
            evidence=("Affects: " + ", ".join(dict.fromkeys(pages))) if pages else "",
            impact=len(items),
        ))
    return fixes


def content_fixes(question_results: list[QuestionResult]) -> list[Fix]:
    fixes: list[Fix] = []
    missing_pages = [qr.question for qr in question_results
                     if qr.gap and qr.gap.own_is_fallback and qr.own_cited_runs == 0]
    if missing_pages:
        fixes.append(Fix(
            rule_id="content:missing-page",
            priority="high",
            title="Create pages that answer these questions",
            detail="You have no page focused on these questions, so there's nothing for an AI engine to cite. "
                   "Write one page per question (or per theme) that answers it directly.",
            evidence="; ".join(f"“{q}”" for q in missing_pages),
            impact=len(missing_pages),
        ))

    # (page, feature) -> questions where the winning pages had it and this page didn't
    gaps: dict[tuple[str, str], list[tuple[str, int, int]]] = defaultdict(list)
    for qr in question_results:
        if not qr.gap or qr.own_cited_runs > 0 or not qr.gap.own_url:
            continue
        for g in qr.gap.gaps:
            if g.is_gap:
                gaps[(qr.gap.own_url, g.key)].append((qr.question, g.winners_with, g.winners_total))
    for (page, key), hits in gaps.items():
        with_it = sum(h[1] for h in hits)
        total = sum(h[2] for h in hits)
        fixes.append(Fix(
            rule_id=f"content:{key}",
            priority="low" if key == "schema" else "medium",
            title=f"{FEATURE_LABELS[key]}: missing on {page}",
            detail=FEATURE_ADVICE[key],
            evidence=f"{with_it} of {total} pages AI engines cited instead have this. Questions: "
                     + "; ".join(f"“{h[0]}”" for h in hits),
            impact=len(hits),
        ))
    return fixes


def retrieval_fixes(question_results: list[QuestionResult]) -> list[Fix]:
    """Split lost questions into "search never showed you" vs "the AI saw you and chose others"."""
    lost = [qr for qr in question_results if qr.retrieval_known and qr.own_cited_runs == 0 and qr.ok_runs > 0]
    unseen = [qr for qr in lost if qr.own_retrieved_rank is None]
    seen = [qr for qr in lost if qr.own_retrieved_rank is not None]
    fixes = []
    if unseen:
        fixes.append(Fix(
            rule_id="retrieval:not-found",
            priority="high",
            title="Get into the search results first",
            detail="For these questions your site wasn't in the search results the AI read, and an AI can't "
                   "cite a page it never sees. This is classic SEO work: a page that targets the question, "
                   "internal links to it, and mentions or links from other sites.",
            evidence="Not in the top results for: " + "; ".join(f"“{qr.question}”" for qr in unseen),
            impact=len(unseen),
        ))
    if seen:
        fixes.append(Fix(
            rule_id="retrieval:not-chosen",
            priority="medium",
            title="The AI saw your page but cited others",
            detail="Your page was in the search results, but the AI chose other sources. Compare your page with "
                   "the cited ones below: direct answers, numbers and fresh dates usually make the difference.",
            evidence="; ".join(f"#{qr.own_retrieved_rank} in results for “{qr.question}”" for qr in seen),
            impact=len(seen),
        ))
    return fixes


def memory_fix(memory: list[MemoryResult]) -> Fix | None:
    answered = [m for m in memory if m.mentioned is not None]
    if not answered or any(m.mentioned for m in answered):
        return None
    return Fix(
        rule_id="memory:unknown",
        priority="medium",
        title="AI models don't name you from memory",
        detail="Asked without web search, the AI didn't mention you once. Models learn brands from pages "
               "crawled for training: earn mentions on well-known sites, keep a clear About page, and check "
               "your robots.txt doesn't block AI training crawlers you'd rather allow.",
        evidence=f"Mentioned in 0 of {len(answered)} answers ({answered[0].model})",
        impact=len(answered),
    )


def off_site_fix(question_results: list[QuestionResult]) -> Fix | None:
    """When AI engines mostly cite reviews, communities and media for the questions you lose."""
    kinds: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    total = 0
    for qr in question_results:
        if qr.own_cited_runs > 0:
            continue
        for dom, count in qr.top_domains:
            total += count
            kind = third_party_kind(dom)
            if kind:
                kinds[kind] += count
                domains[dom] += count
    third = sum(kinds.values())
    if not total or third / total < OFF_SITE_SHARE:
        return None
    top = ", ".join(d for d, _ in domains.most_common(6))
    return Fix(
        rule_id="offsite:third-party",
        priority="off-site",
        title="Get mentioned on the sites AI engines trust",
        detail="For the questions you lose, AI answers lean on review sites, communities and media rather "
               "than company websites. Earn real reviews, take part in discussions honestly, and pitch "
               "your data to journalists. Don't buy links or fake reviews.",
        evidence=f"{third} of {total} cited sources ({third / total:.0%}) were third-party: {top}",
        impact=third,
    )


def competitor_fix(stats: list[DomainStat], competitors: list[str]) -> Fix | None:
    ranked = [s for s in stats if s.domain in competitors and s.runs_cited > 0]
    if not ranked:
        return None
    lead = ranked[0]
    own = next((s for s in stats if s.is_own), None)
    if own and own.citation_rate >= lead.citation_rate:
        return None
    return Fix(
        rule_id="competitor:study",
        priority="low",
        title=f"Study what {lead.domain} publishes",
        detail="The competitor cited most often shows what AI engines find useful in your category. "
               "Look at the pages of theirs that were cited and cover the same questions better.",
        evidence=f"{lead.domain} was cited in {lead.citation_rate:.0%} of answers"
                 + (f"; you were cited in {own.citation_rate:.0%}" if own else ""),
    )


def recommend(
    technical: list[TechCheck],
    question_results: list[QuestionResult],
    stats: list[DomainStat],
    competitors: list[str],
    visibility_score: float | None,
    memory: list[MemoryResult] | None = None,
) -> list[Fix]:
    fixes = technical_fixes(technical) + content_fixes(question_results) + retrieval_fixes(question_results)
    for extra in (off_site_fix(question_results), competitor_fix(stats, competitors), memory_fix(memory or [])):
        if extra:
            fixes.append(extra)
    if visibility_score is not None and visibility_score >= 80 and not any(
        f.priority == "high" for f in fixes
    ):
        fixes.append(Fix(rule_id="keep:strong", priority="low", title="You're well cited. Keep it that way",
                         detail="Re-run this report monthly and after big site changes to catch drops early.",
                         evidence=f"Visibility score {visibility_score:.0f}/100"))
    fixes.sort(key=lambda f: (PRIORITY_ORDER[f.priority], -f.impact, f.rule_id))
    return fixes
