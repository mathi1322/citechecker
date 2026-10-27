"""Tell competitors apart from third-party sites (reviews, communities, media) in the cited sources."""

from __future__ import annotations

from app.citations import registrable_domain

# Sites AI engines cite *about* companies. You can't edit them, but you can be listed, reviewed or
# discussed on them. That is the "off-site" fix.
THIRD_PARTY: dict[str, str] = {
    **dict.fromkeys(["g2.com", "capterra.com", "trustradius.com", "gartner.com", "getapp.com",
                     "softwareadvice.com", "trustpilot.com", "clutch.co", "producthunt.com",
                     "sourceforge.net", "peerspot.com", "softwarereviews.com"], "review site"),
    **dict.fromkeys(["reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com",
                     "news.ycombinator.com", "ycombinator.com", "medium.com", "dev.to", "substack.com"],
                    "community"),
    **dict.fromkeys(["youtube.com", "linkedin.com", "facebook.com", "x.com", "twitter.com",
                     "instagram.com", "tiktok.com"], "social / video"),
    **dict.fromkeys(["wikipedia.org", "britannica.com"], "encyclopedia"),
    **dict.fromkeys(["yahoo.com", "msn.com", "aol.com", "bing.com", "google.com"], "portal"),
    **dict.fromkeys(["prnewswire.com", "businesswire.com", "globenewswire.com", "einpresswire.com"],
                    "press release"),
    **dict.fromkeys(["researchgate.net", "sciencedirect.com", "springer.com", "arxiv.org", "nature.com",
                     "mckinsey.com", "deloitte.com", "gartner.com", "statista.com", "precedenceresearch.com",
                     "marketsandmarkets.com", "grandviewresearch.com", "fortunebusinessinsights.com",
                     "mordorintelligence.com", "imarcgroup.com"], "research"),
    **dict.fromkeys(["forbes.com", "techcrunch.com", "zdnet.com", "techradar.com", "pcmag.com",
                     "businessinsider.com", "nytimes.com", "theverge.com", "wired.com", "cnbc.com",
                     "hbr.org", "economictimes.indiatimes.com", "indiatimes.com", "inc.com",
                     "entrepreneur.com"], "media"),
}


# Public-sector and education domains are sources, not competitors.
INSTITUTION_SUFFIXES = (".edu", ".gov", ".mil", ".ac.uk", ".gov.uk", ".edu.au", ".gov.in", ".ac.in", ".nic.in")


def third_party_kind(domain: str) -> str | None:
    dom = registrable_domain(domain)
    if dom.endswith(INSTITUTION_SUFFIXES) or dom in {s.lstrip(".") for s in INSTITUTION_SUFFIXES}:
        return "education / government"
    return THIRD_PARTY.get(dom)


def is_third_party(domain: str) -> bool:
    return third_party_kind(domain) is not None


def normalize_competitors(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        dom = registrable_domain(item)
        if dom and "." in dom and dom not in out:
            out.append(dom)
    return out


def derive_competitors(
    ranked_domains: list[str], own_domain: str, user_given: list[str] | None = None, limit: int = 5
) -> list[str]:
    """User-given competitors first, then the most-cited domains that aren't you or third-party sites."""
    out = [d for d in normalize_competitors(user_given) if d != own_domain]
    added = 0
    for dom in ranked_domains:
        if added >= limit:
            break
        if dom != own_domain and not is_third_party(dom) and dom not in out:
            out.append(dom)
            added += 1
    return out
