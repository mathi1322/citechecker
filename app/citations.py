"""URL and domain handling: is this cited link "your site"?"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import tldextract

from app.models import Citation

# Offline extractor: uses the public-suffix snapshot bundled with tldextract, no network call.
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())

GOOGLE_REDIRECT_HOSTS = {"vertexaisearch.cloud.google.com"}
_TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "ref", "ref_src", "mc_cid", "mc_eid", "srsltid"}
_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def registrable_domain(url_or_host: str) -> str:
    """'https://blog.www.example.co.uk/x' -> 'example.co.uk'. Hosts without a public suffix are returned as-is."""
    text = url_or_host.strip().lower()
    if "://" not in text:
        text = "http://" + text
    host = (urlsplit(text).hostname or "").strip(".")
    if not host:
        return ""
    ext = _EXTRACT(host)
    return ext.top_domain_under_public_suffix or host


def normalize_site(raw: str) -> tuple[str, str]:
    """Turn user input like 'Example.com' or 'https://www.example.com/about' into (base_url, domain).

    Raises ValueError with a message fit to show the user.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Enter a website, for example yourcompany.com")
    if " " in text:
        raise ValueError("A website address can't contain spaces")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("Only http and https websites are supported")
    host = (parts.hostname or "").lower().strip(".")
    if not host or "." not in host or not _HOST_RE.match(host):
        raise ValueError(f"{raw!r} doesn't look like a website address")
    ext = _EXTRACT(host)
    if not ext.suffix:
        raise ValueError(f"{raw!r} doesn't have a real domain ending (like .com)")
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}", registrable_domain(host)


def normalize_url(url: str) -> str:
    """Comparable form of a URL: lowercase host, no fragment, no tracking params, no trailing slash."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # http and https copies of a page count as the same page.
    return urlunsplit(("https", host, path, urlencode(query), ""))


def is_own(url_or_domain: str, own_domain: str) -> bool:
    return bool(own_domain) and registrable_domain(url_or_domain) == own_domain.lower()


def _looks_like_domain(text: str) -> bool:
    text = text.strip().lower()
    return bool(text) and " " not in text and "." in text and bool(_EXTRACT(text).suffix)


def make_citation(url: str, title: str, position: int) -> Citation:
    """Build a Citation. Google's grounding titles are usually the source domain, which is
    used when the URL is still an unresolved redirect link."""
    host = (urlsplit(url).hostname or "").lower()
    if host in GOOGLE_REDIRECT_HOSTS and _looks_like_domain(title):
        domain = registrable_domain(title)
    else:
        domain = registrable_domain(url)
    return Citation(url=url, title=title, domain=domain, position=position)


def dedupe_citations(citations: list[Citation]) -> list[Citation]:
    """Keep the first citation of each URL and renumber positions 1..n."""
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        key = normalize_url(c.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(c.model_copy(update={"position": len(out) + 1}))
    return out


async def resolve_redirect(client: httpx.AsyncClient, url: str) -> str:
    """Gemini cites sources through vertexaisearch.cloud.google.com redirect links.
    Read the Location header to get the real page. On any failure, keep the original link."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in GOOGLE_REDIRECT_HOSTS:
        return url
    try:
        resp = await client.get(url, follow_redirects=False)
    except httpx.HTTPError:
        return url
    location = resp.headers.get("location")
    if 300 <= resp.status_code < 400 and location:
        return str(resp.url.join(location))
    return url


async def resolve_all(client: httpx.AsyncClient, urls: list[str]) -> list[str]:
    return list(await asyncio.gather(*(resolve_redirect(client, u) for u in urls)))
