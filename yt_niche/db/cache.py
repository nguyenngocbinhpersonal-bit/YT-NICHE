"""API response cache by key + time to live."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any


def make_key(namespace: str, params: dict) -> str:
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return f"{namespace}:{hashlib.sha1(raw.encode()).hexdigest()}"


def get(conn: sqlite3.Connection, key: str) -> Any | None:
    row = conn.execute("SELECT response, expires_at FROM api_cache WHERE key = ?", (key,)).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return json.loads(row["response"])


def put(conn: sqlite3.Connection, key: str, value: Any, ttl_seconds: float) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO api_cache (key, namespace, response, expires_at) VALUES (?, ?, ?, ?)",
        (key, key.split(":", 1)[0], json.dumps(value, ensure_ascii=False), time.time() + ttl_seconds),
    )


def purge_expired(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM api_cache WHERE expires_at < ?", (time.time(),)).rowcount
