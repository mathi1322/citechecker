"""SQLite storage for reports, the answer cache and daily counters. Single file, no server."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models import Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_domain ON reports(domain, created_at);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters (name TEXT NOT NULL, day TEXT NOT NULL, value INTEGER NOT NULL,
                                     PRIMARY KEY (name, day));
"""


class Storage:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # reports
    def save_report(self, report: Report) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports (id, domain, created_at, status, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
                (report.id, report.domain, report.created_at.isoformat(), report.status,
                 report.model_dump_json()),
            )

    def get_report(self, report_id: str) -> Report | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM reports WHERE id = ?", (report_id,)).fetchone()
        return Report.model_validate_json(row[0]) if row else None

    def list_reports(self, domain: str, limit: int = 20) -> list[Report]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM reports WHERE domain = ? ORDER BY created_at DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        return [Report.model_validate_json(r[0]) for r in rows]

    def mark_stale_running(self) -> int:
        """Reports left 'running' by a restart can never finish. Mark them failed."""
        stale = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM reports WHERE status IN ('queued', 'running')").fetchall()
        for (data,) in rows:
            report = Report.model_validate_json(data)
            report.status, report.error = "error", "The server restarted while this report was running."
            stale.append(report)
        for report in stale:
            self.save_report(report)
        return len(stale)

    # answer cache
    def cache_get(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
            )

    # counters
    def consume(self, name: str, day: str, n: int, limit: int) -> bool:
        """Add n to today's counter if it stays within limit. Atomic. Returns False if it wouldn't fit."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT value FROM counters WHERE name = ? AND day = ?", (name, day)).fetchone()
            current = row[0] if row else 0
            if current + n > limit:
                return False
            self._conn.execute(
                "INSERT INTO counters (name, day, value) VALUES (?, ?, ?) "
                "ON CONFLICT(name, day) DO UPDATE SET value = excluded.value",
                (name, day, current + n),
            )
            return True

    def count(self, name: str, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM counters WHERE name = ? AND day = ?", (name, day)).fetchone()
        return row[0] if row else 0
