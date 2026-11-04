import json
import time
from datetime import datetime, timezone

from yt_niche.core.confidence import HIGH, MEDIUM
from yt_niche.core.format_split import FIT_LABEL
from yt_niche.core.money import RPM_MEDIUM
from yt_niche.core.niche_score import LABEL_GREEN
from yt_niche.core.outlier import LEVEL_VIRAL
from yt_niche.core.three_tier import STRONG, TRY, WEAK
from yt_niche.core.trends_classify import SEASONAL, UP
from yt_niche.db import cache, quota
from yt_niche.db.connection import LANG_MIGRATION_FLAG, _migrate, connect
from yt_niche.services.fetch import published_after


def test_cache_roundtrip_and_expiry(conn):
    key = cache.make_key("videos", {"id": "a,b", "part": "x"})
    assert key == cache.make_key("videos", {"part": "x", "id": "a,b"})  # order independent
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
    # 05:00 UTC on the 2nd = 22:00 PDT on the 1st
    assert quota.quota_day(datetime(2026, 10, 2, 5, 0, tzinfo=timezone.utc)) == "2026-10-01"


def test_published_after_rounds_to_day():
    now = datetime(2026, 10, 17, 15, 42, 7, tzinfo=timezone.utc)
    assert published_after(30, now) == "2026-09-17T00:00:00Z"


# ---------------------------------------------------------------------------
# One-time migration of stored Vietnamese values to the English constants
# ---------------------------------------------------------------------------

OLD_RESULT = {
    "labels": ["Ngách xanh"],
    "confidence": "cao",
    "rpm_tier": "vừa",
    "three_tier": {
        "demand": {"level": "Mạnh", "reason": "Nhu cầu 70/100, Trends: theo mùa"},
        "supply": {"level": "Yếu", "reason": "free text stays"},
        "verdict": "THỬ",
    },
    "trends": {"class": "theo mùa", "direction": "đi lên", "reason": "nền 5 năm đi lên"},
    "outliers": [{"video_id": "v1", "level": "Nổ view", "title": "Vượt trội"}],
    "format_split": {"fit": "long", "fit_label": "Ngách hợp video dài"},
    "note": "kể chuyện lịch sử — THỬ",
}


def _insert_old(conn, topic="x"):
    conn.execute(
        "INSERT INTO dig_run (topic, region, language, window_days, fmt, category, created_at, label, confidence, verdict, "
        "raw_json, result_json) VALUES (?, 'VN', 'vi', 30, 'any', 'Lịch sử', '2026-10-01', 'Ngách xanh', 'cao', 'THỬ', '{}', ?)",
        (topic, json.dumps(OLD_RESULT, ensure_ascii=False)),
    )
    return conn.execute("SELECT id FROM dig_run WHERE topic = ?", (topic,)).fetchone()["id"]


def test_language_migration_maps_old_values_once(tmp_path):
    path = tmp_path / "old.db"
    conn = connect(path)
    conn.execute("DELETE FROM app_meta WHERE key = ?", (LANG_MIGRATION_FLAG,))  # pretend an old DB
    rid = _insert_old(conn)
    conn.execute("INSERT INTO market_batch (topic, category, window_days, fmt, created_at) VALUES ('t', 'Tâm linh / tôn giáo', 30, 'any', 'now')")
    conn.execute(
        "INSERT INTO radar_keyword (region, keyword, category_id, kind, first_seen, last_seen, trend_class) "
        "VALUES ('VN', 'k', 1, 'top', 'd', 'd', 'bền')"
    )
    conn.execute("INSERT INTO market (region, name, hl, group_name, updated_at) VALUES ('US', 'Mỹ', 'en', 'Tier 1 (RPM cao)', 'now')")
    conn.execute("INSERT INTO market (region, name, hl, group_name, updated_at) VALUES ('ES', 'Tây Ban Nha', 'es', 'Khác', 'now')")
    conn.close()

    conn = connect(path)  # reopening runs _migrate → _migrate_language
    row = conn.execute("SELECT label, confidence, verdict, category, result_json FROM dig_run WHERE id = ?", (rid,)).fetchone()
    assert (row["label"], row["confidence"], row["verdict"], row["category"]) == (LABEL_GREEN, HIGH, TRY, "History")
    r = json.loads(row["result_json"])
    assert r["labels"] == [LABEL_GREEN] and r["confidence"] == HIGH and r["rpm_tier"] == RPM_MEDIUM
    assert r["three_tier"]["demand"]["level"] == STRONG and r["three_tier"]["supply"]["level"] == WEAK
    assert r["three_tier"]["verdict"] == TRY and r["trends"]["class"] == SEASONAL and r["trends"]["direction"] == UP
    assert r["outliers"][0]["level"] == LEVEL_VIRAL and r["format_split"]["fit_label"] == FIT_LABEL["long"]
    # free text and other leaves are untouched
    assert r["three_tier"]["demand"]["reason"] == "Nhu cầu 70/100, Trends: theo mùa"
    assert r["outliers"][0]["title"] == "Vượt trội" and r["note"] == "kể chuyện lịch sử — THỬ"
    assert conn.execute("SELECT category FROM market_batch").fetchone()[0] == "Spirituality / religion"
    assert conn.execute("SELECT trend_class FROM radar_keyword").fetchone()[0] == "evergreen"
    markets = {m["region"]: (m["name"], m["group_name"]) for m in conn.execute("SELECT * FROM market")}
    assert markets["US"] == ("United States", "Tier 1 (high RPM)")
    assert markets["ES"] == ("Tây Ban Nha", "Other")  # unknown name untouched, known group mapped
    assert conn.execute("SELECT value FROM app_meta WHERE key = ?", (LANG_MIGRATION_FLAG,)).fetchone()[0] == "1"

    # The flag stops a second run from touching anything.
    rid2 = _insert_old(conn, topic="y")
    _migrate(conn)
    conn.close()
    conn = connect(path)
    row = conn.execute("SELECT label, confidence, verdict, category, result_json FROM dig_run WHERE id = ?", (rid2,)).fetchone()
    assert (row["label"], row["confidence"], row["verdict"], row["category"]) == ("Ngách xanh", "cao", "THỬ", "Lịch sử")
    assert json.loads(row["result_json"]) == OLD_RESULT
    conn.close()


def test_language_migration_on_fresh_db_sets_flag_only(conn):
    assert conn.execute("SELECT value FROM app_meta WHERE key = ?", (LANG_MIGRATION_FLAG,)).fetchone()[0] == "1"
    assert conn.execute("SELECT COUNT(*) FROM dig_run").fetchone()[0] == 0
    assert MEDIUM == "medium"  # the constants the migration targets are the shared glossary values
