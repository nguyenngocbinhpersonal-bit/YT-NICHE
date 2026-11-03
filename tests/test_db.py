import time
from datetime import datetime, timezone

from yt_niche.db import cache, quota
from yt_niche.services.fetch import published_after


def test_cache_roundtrip_and_expiry(conn):
    key = cache.make_key("videos", {"id": "a,b", "part": "x"})
    assert key == cache.make_key("videos", {"part": "x", "id": "a,b"})  # không phụ thuộc thứ tự
    cache.put(conn, key, {"items": [1]}, ttl_seconds=60)
    assert cache.get(conn, key) == {"items": [1]}

    cache.put(conn, key, {"items": [2]}, ttl_seconds=-1)
    assert cache.get(conn, key) is None
    assert cache.purge_expired(conn) == 1


def test_quota_record_and_sum(conn):
    quota.record(conn, "search", 100)
    quota.record(conn, "videos", 1)
    assert quota.used_on(conn) == 101
    assert quota.used_on(conn, "2000-01-01") == 0


def test_quota_day_uses_pacific_time():
    # 05:00 UTC ngày 2 = 22:00 PDT ngày 1
    assert quota.quota_day(datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)) == "2026-10-01"


def test_published_after_rounds_to_day():
    now = datetime(2026, 10, 17, 15, 42, 7, tzinfo=timezone.utc)
    assert published_after(30, now) == "2026-09-17T00:00:00Z"
