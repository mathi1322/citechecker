"""Keep a public, no-login demo from burning through the free API quota."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import Request

from app.storage import Storage


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class RateLimiter:
    """Sliding window: at most `limit` actions per `window` seconds per key (visitor IP)."""

    def __init__(self, limit: int, window: float = 3600.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str) -> int:
        hits = self._hits.get(key)
        if not hits:
            return 0
        return max(0, int(self.window - (self.clock() - hits[0])) + 1)


class DailyBudget:
    """Real AI calls allowed per UTC day across all visitors."""

    NAME = "engine_calls"

    def __init__(self, storage: Storage, limit: int, day: Callable[[], str] = utc_day):
        self.storage = storage
        self.limit = limit
        self.day = day

    def try_consume(self, n: int = 1) -> bool:
        return self.storage.consume(self.NAME, self.day(), n, self.limit)

    def used(self) -> int:
        return self.storage.count(self.NAME, self.day())

    def remaining(self) -> int:
        return max(0, self.limit - self.used())


def client_ip(request: Request, trust_proxy: bool = True) -> str:
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
