"""One place for fetching web pages, so every caller gets the same limits and error handling."""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "CiteCheckBot/0.1 (+https://github.com/mathi1322/citechecker; AI-search visibility checker)"
ROBOTS_TOKEN = "CiteCheckBot"
MAX_BYTES = 3_000_000


class BlockedURL(httpx.RequestError):
    """The URL points somewhere this server must not fetch (internal network, metadata service…)."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


async def resolve(host: str) -> list[str]:
    """All IP addresses for a hostname. Tests replace this so they never touch real DNS."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def check_public(request: httpx.Request) -> None:
    """httpx request hook: refuse anything that isn't http(s) to a public address.

    Runs for every request, including each redirect hop, so a sitemap or redirect can't send the
    server to internal addresses (SSRF). A DNS answer could still change between this check and the
    connection; that rebinding window is accepted for a demo tool.
    """
    if request.url.scheme not in ("http", "https"):
        raise BlockedURL(f"blocked scheme: {request.url.scheme}", request=request)
    host = request.url.host
    try:
        addresses = [host] if _is_ip(host) else await resolve(host)
    except OSError as exc:
        raise httpx.ConnectError(f"DNS lookup failed for {host}", request=request) from exc
    if not addresses or not all(_is_public(ipaddress.ip_address(a.split("%")[0])) for a in addresses):
        raise BlockedURL(f"blocked non-public address for {host}", request=request)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    error: str | None = None  # "timeout", "redirect_loop", "blocked", "connect: …", "http_error", "robots"
    redirects: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


def make_client(
    timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None, guard: bool = True
) -> httpx.AsyncClient:
    """HTTP client for everything the server fetches. `guard` blocks internal addresses (keep it on)."""
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        max_redirects=8,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        transport=transport,
        event_hooks={"request": [check_public]} if guard else {},
    )


def _decode(content: bytes, encoding: str | None) -> str:
    # Sitemaps are often served gzipped without a Content-Encoding header.
    if content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except OSError:
            return ""
    return content.decode(encoding or "utf-8", errors="replace")


async def fetch(client: httpx.AsyncClient, url: str) -> FetchResult:
    try:
        resp = await client.get(url)
    except BlockedURL:
        return FetchResult(url=url, final_url=url, status=None, error="blocked")
    except httpx.TooManyRedirects:
        return FetchResult(url=url, final_url=url, status=None, error="redirect_loop")
    except httpx.TimeoutException:
        return FetchResult(url=url, final_url=url, status=None, error="timeout")
    except httpx.HTTPError as exc:
        return FetchResult(url=url, final_url=url, status=None, error=f"connect: {type(exc).__name__}")
    content = resp.content
    if len(content) > MAX_BYTES:
        content = content[:MAX_BYTES]
    return FetchResult(
        url=url,
        final_url=str(resp.url),
        status=resp.status_code,
        headers={k.lower(): v for k, v in resp.headers.items()},
        text=_decode(content, resp.encoding),
        redirects=len(resp.history),
        error=None if resp.status_code < 400 else "http_error",
    )


class RobotsCache:
    """robots.txt rules per site, so fetching other sites' pages follows their wishes.

    If a site's robots.txt can't be read (404, error), fetching is allowed, as crawlers usually do.
    """

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._rules: dict[str, RobotFileParser | None] = {}

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._rules:
            res = await fetch(self.client, origin + "/robots.txt")
            if res.ok and res.text.strip():
                parser = RobotFileParser()
                parser.parse(res.text.splitlines())
                self._rules[origin] = parser
            else:
                self._rules[origin] = None
        rules = self._rules[origin]
        return rules is None or rules.can_fetch(ROBOTS_TOKEN, url)


async def polite_fetch(client: httpx.AsyncClient, robots: RobotsCache, url: str) -> FetchResult:
    if not await robots.allowed(url):
        return FetchResult(url=url, final_url=url, status=None, error="robots")
    return await fetch(client, url)
