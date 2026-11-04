"""Record and read the YouTube API units used per quota day."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from yt_niche.config import QUOTA_TIMEZONE


def quota_day(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo(QUOTA_TIMEZONE))
    return now.astimezone(ZoneInfo(QUOTA_TIMEZONE)).strftime("%Y-%m-%d")


def record(conn: sqlite3.Connection, endpoint: str, units: int) -> None:
    conn.execute(
        "INSERT INTO quota_log (day, endpoint, units, created_at) VALUES (?, ?, ?, ?)",
        (quota_day(), endpoint, units, time.time()),
    )


def used_on(conn: sqlite3.Connection, day: str | None = None) -> int:
    row = conn.execute("SELECT COALESCE(SUM(units), 0) FROM quota_log WHERE day = ?", (day or quota_day(),)).fetchone()
    return int(row[0])
