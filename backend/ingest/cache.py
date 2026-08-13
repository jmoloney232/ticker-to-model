"""SQLite runtime cache — tier 2 of the degradation ladder (spec 00).

Stores raw EDGAR JSON payloads (zlib-compressed) keyed by endpoint+CIK. The cache
never expires destructively: a stale entry is still served (labeled stale) when the
live API is down.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from pathlib import Path


class SqliteCache:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body BLOB NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str, max_age_s: float) -> tuple[dict, bool] | None:
        """Return (payload, is_fresh) or None if absent."""
        row = self._conn.execute(
            "SELECT fetched_at, body FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        fetched_at, body = row
        payload = json.loads(zlib.decompress(body))
        return payload, (time.time() - fetched_at) <= max_age_s

    def put(self, key: str, payload: dict) -> None:
        body = zlib.compress(json.dumps(payload, separators=(",", ":")).encode())
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, fetched_at, body) VALUES (?, ?, ?)",
            (key, time.time(), body),
        )
        self._conn.commit()


class NullCache:
    """No-op cache for tests and one-shot scripts."""

    def get(self, key: str, max_age_s: float) -> None:
        return None

    def put(self, key: str, payload: dict) -> None:
        pass
