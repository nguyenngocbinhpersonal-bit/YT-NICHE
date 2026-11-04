"""Keyword radar: category choice, short-lived filter, spread-out ticks, YouTube scoring, self-added markets."""

from datetime import date, datetime, timedelta, timezone

import pytest

from yt_niche import config as cfg
from yt_niche.core import radar as rd
from yt_niche.services import market_catalog as mc
from yt_niche.services import radar
from yt_niche.sources import trends

TREE = {
    "id": 0, "name": "All categories",
    "children": [
        {"id": 3, "name": "Arts & Entertainment", "children": [
            {"id": 184, "name": "Celebrities & Entertainment News"},
            {"id": 316, "name": "Comics & Animation"},
        ]},
        {"id": 16, "name": "News", "children": [{"id": 63, "name": "Weather"}]},
        {"id": 66, "name": "Pets & Animals", "children": []},
    ],
}
NOW = datetime(2026, 10, 18, 2, 0, tzinfo=timezone.utc)


def _daily(values):
    start = date(2026, 9, 18)
    return [[(start + timedelta(days=i)).isoformat(), v] for i, v in enumerate(values)]


STEADY_UP = _daily([50 + i for i in range(31)])
SPARSE = _daily([0] * 20 + [100] + [0] * 10)
SPIKY = _daily([5] * 30 + [100])  # present every day but concentrated in one day: low average


@pytest.fixture
def fake(monkeypatch):
    calls = []
    found = {
        "rising": [("dog training tips", 900), ("katseye", 9500), ("xổ số miền nam", 5000), ("bão narra", 150)],
        "top": [("dog", 100), ("dog training tips", 40)],
    }
    entities = {"katseye": [{"title": "KATSEYE", "type": "Girl group"}]}
    series = {"dog training tips": {"youtube": STEADY_UP, "web": STEADY_UP}, "dog": {"web": STEADY_UP}}

    monkeypatch.setattr(trends, "category_tree", lambda conn: TREE)
    monkeypatch.setattr(trends, "category_queries", lambda geo, cat: calls.append(("discover", geo, cat)) or found)
    monkeypatch.setattr(trends, "entity_types", lambda q: calls.append(("entity", q)) or entities.get(q, []))
    monkeypatch.setattr(
        trends, "series_30d", lambda q, geo, source="web": calls.append(("series", q, source)) or series.get(q, {}).get(source, [])
    )
    monkeypatch.setattr(cfg, "RADAR_PER_SOURCE", 2)
    monkeypatch.setattr(cfg, "RADAR_INTERVAL_S", 240)
    monkeypatch.setattr(cfg, "api_key", lambda: "")
    return calls


@pytest.fixture
def us_only(conn, fake):
    radar.save_scope(conn, ["US"], [66], rd.DEFAULT_BLOCKLIST, all_markets=False)
    return fake


def test_default_categories_skip_short_term():
    assert rd.default_categories(TREE) == [66, 316]
    assert {c["id"] for c in rd.selectable_categories(TREE)} == {3, 316, 66}


def test_filters():
    assert rd.short_term_entity("katseye", [{"title": "KATSEYE", "type": "Girl group"}])
    assert rd.short_term_entity("nguyễn sỹ cương", [{"title": "Nguyen Sy Cuong", "type": "Politician"}])
    assert rd.short_term_entity("running man mùa 4", [{"title": "Running man", "type": "TV program"}])
    assert not rd.short_term_entity("dog training", [{"title": "Dog training", "type": "Topic"}])
    assert rd.blocked("Xổ Số Miền Nam hôm nay", rd.DEFAULT_BLOCKLIST)
    good = rd.judge_volume({"youtube": rd.volume_30d(STEADY_UP), "web": rd.volume_30d(STEADY_UP)})
    assert good["ok"] and good["durability"] == 100 and good["trend_30d"] > 0
    yt, web = rd.SOURCE_LABEL["youtube"], rd.SOURCE_LABEL["web"]
    none = rd.judge_volume({"youtube": rd.volume_30d([])})
    assert not none["ok"] and none["reason"].startswith(f"{yt}:") and none["volume"]["youtube"]["avg"] == 0
    sparse = rd.judge_volume({"youtube": rd.volume_30d(STEADY_UP), "web": rd.volume_30d(SPARSE)})
    assert not sparse["ok"] and sparse["reason"].startswith(f"{web}:")  # the second source is the one that fails
    assert sparse["volume"]["web"]["zeros"] > cfg.RADAR_30D_MAX_ZERO_SHARE  # too few people searching
    spiky = rd.judge_volume({"youtube": rd.volume_30d(SPIKY)})
    assert not spiky["ok"] and spiky["reason"].startswith(f"{yt}:") and spiky["volume"]["youtube"]["avg"] < cfg.RADAR_30D_MIN_AVG  # low volume


def test_pick_new_prefers_rising():
    found = {"rising": [("a b", 150), ("c d", 5000), ("e f", 300)], "top": [("g h", 100)]}
    picked = rd.pick_new(found, {"e f"}, [], limit=2)
    assert [p["keyword"] for p in picked] == ["c d", "g h"]


def test_potential_weights():
    row = {"status": rd.PASSED, "kind": "rising", "value": 1000, "seen_days": 1, "trend": {"ok": True, "durability": 100}}
    without = rd.potential(row)
    assert without == pytest.approx((0.35 * 50 + 0.25 * 100) / 0.75, abs=0.1)
    assert rd.potential({**row, "yt_score": 0}) < without
    assert rd.potential({**row, "trend": {"ok": False}}) is None


def test_tick_flow(conn, us_only):
    fake = us_only
    r = radar.tick(conn, now=NOW)
    assert r["action"] == "discover" and fake[0][0] == "discover"
    pending = {k["keyword"] for k in radar.keywords(conn, [rd.PENDING])}
    assert pending == {"katseye", "dog training tips"}  # lottery is blocklisted, the storm is under +200%, only 2 new

    assert radar.tick(conn, now=NOW + timedelta(seconds=10))["action"] == "skip"  # not due yet

    t = NOW
    for _ in range(4):
        t += timedelta(minutes=10)
        radar.tick(conn, now=t)
    passed = radar.keywords(conn, [rd.PASSED])
    excluded = {k["keyword"]: k for k in radar.keywords(conn, [rd.EXCLUDED])}
    assert [k["keyword"] for k in passed] == ["dog training tips"] and passed[0]["potential"] > 0
    assert "Girl group" in excluded["katseye"]["reason"]
    assert not any(c[:2] == ("series", "katseye") for c in fake)  # dropped at the entity step, no Trends request spent
    assert passed[0]["trend"]["volume"]["youtube"]["ok"] and passed[0]["trend"]["volume"]["web"]["ok"]

    # Seen again the next day → seen_days grows, potential grows
    before = passed[0]["potential"]
    conn.execute("DELETE FROM radar_source")
    radar.tick(conn, now=NOW + timedelta(days=1), force=True)
    again = radar.keywords(conn, [rd.PASSED])[0]
    assert again["seen_days"] == 2 and again["potential"] > before
    assert radar.stats(conn, NOW + timedelta(days=1))["pairs"] == 1
    conn.execute("DELETE FROM radar_meta WHERE key = 'scope'")
    assert radar.get_scope(conn)["all_markets"] and radar.stats(conn)["pairs"] == len(cfg.MARKETS) * 2


def test_tick_throttled_cools_down(conn, fake, monkeypatch):
    def boom(geo, cat):
        raise trends.Throttled("429")

    monkeypatch.setattr(trends, "category_queries", boom)
    assert radar.tick(conn, now=NOW)["action"] == "cooldown"
    assert trends.cooldown_until(conn)
    assert radar.tick(conn, now=NOW + timedelta(minutes=5), force=True)["action"] == "cooldown"


def test_lock_blocks_parallel_tick(conn, fake):
    assert radar._acquire(conn, NOW)
    assert radar.tick(conn, now=NOW, force=True)["detail"] == "another tick is running"
    radar._release(conn)


def test_youtube_scoring_budget(conn, us_only, monkeypatch):
    monkeypatch.setattr(cfg, "api_key", lambda: "k")
    monkeypatch.setattr(cfg, "RADAR_YT_UNITS_PER_DAY", 210)
    scored = []

    async def fake_score(conn_, region, kws, fresh):
        scored.append(kws[0])
        return [{"keyword": kws[0], "score": 80, "demand": 70, "competition": 10, "n": 40, "low_data": False,
                 "median_views": 5000, "big_share": 0.1, "exact_share": 0.1, "label": "Very good"}], 102

    monkeypatch.setattr("yt_niche.services.keywords._score_many", fake_score)
    t = NOW
    for _ in range(5):
        radar.tick(conn, now=t)
        t += timedelta(minutes=10)
    assert scored == ["dog training tips"]
    assert radar.keywords(conn, [rd.PASSED])[0]["yt_score"] == 80
    assert radar.yt_units_today(conn, t) == 102


def test_market_table_is_single_source(conn):
    assert mc.relevance_language("es-419") == "es" and mc.relevance_language("iw") == "he"
    assert mc.relevance_language("zh-TW") == "zh-Hant" and mc.default_hl("TW") == "zh-TW"
    mc.apply(conn)  # first time: the 17 default markets
    assert cfg.ENABLED_MARKETS == list(cfg.DEFAULT_MARKETS) and cfg.MARKETS["TW"] == ("zh-Hant", "Taiwan")
    try:
        mc.save(conn, [
            {"region": "ES", "name": "Spain", "hl": "es", "group_name": "Europe", "enabled": True},
            {"region": "KR", "name": "South Korea", "hl": "ko", "group_name": "East Asia", "enabled": False},
            {"region": "XX", "name": "Not enabled", "hl": "en", "group_name": "", "enabled": False},
        ])
        assert "ES" in cfg.ENABLED_MARKETS and cfg.MARKET_GROUPS["Europe"] == ["ES"]
        assert "KR" not in cfg.ENABLED_MARKETS and cfg.MARKETS["KR"][1] == "South Korea"  # disabled but the name is still there
        assert "XX" not in cfg.MARKETS  # a region never enabled is not stored
        with pytest.raises(mc.MarketError):
            mc.save(conn, [{"region": r, "name": "x", "hl": "en", "enabled": False} for r in cfg.MARKETS])
        radar.save_scope(conn, [], [66], [], all_markets=True)
        assert "ES" in radar._markets(conn, radar.get_scope(conn)) and "KR" not in radar._markets(conn, radar.get_scope(conn))
    finally:
        conn.execute("DELETE FROM market")
        mc.apply(conn)
    assert cfg.ENABLED_MARKETS == list(cfg.DEFAULT_MARKETS) and "ES" not in cfg.MARKETS


def test_old_custom_markets_migrate(conn):
    conn.execute("INSERT INTO custom_market (region, language, name, added_at) VALUES ('ES', 'es', 'Spain', 'x')")
    try:
        mc.apply(conn)
        assert cfg.MARKETS["ES"] == ("es", "Spain") and "ES" in cfg.ENABLED_MARKETS
    finally:
        conn.execute("DELETE FROM market")
        conn.execute("DELETE FROM custom_market")
        mc.apply(conn)


def test_disabled_market_is_skipped(conn, us_only, monkeypatch):
    radar.tick(conn, now=NOW)  # scan US → 2 keywords pending
    monkeypatch.setattr(cfg, "ENABLED_MARKETS", ["VN"])
    assert radar._next_pending(conn) is None  # US disabled: no check, no request spent
    assert radar.keywords(conn, [rd.PENDING], cfg.ENABLED_MARKETS) == []


def test_pick_new_prefers_rising_that_is_also_top():
    found = {"rising": [("tiny thing", 9000), ("dog training tips", 300)], "top": [("dog training tips", 40)]}
    assert [p["keyword"] for p in rd.pick_new(found, set(), [], limit=1)] == ["dog training tips"]


def test_pace_speeds_up_and_backs_off(conn, monkeypatch):
    monkeypatch.setattr(cfg, "RADAR_SPEEDUP_EVERY", 2)
    assert radar.current_interval(conn) == cfg.RADAR_INTERVAL_S
    for _ in range(2):
        radar._adapt_interval(conn, {"action": "check", "requests": 3})
    assert radar.current_interval(conn) == pytest.approx(cfg.RADAR_INTERVAL_S * 0.8)
    radar._adapt_interval(conn, {"action": "cooldown", "throttled": True})
    assert radar.current_interval(conn) == pytest.approx(cfg.RADAR_INTERVAL_S * 0.8 * 2)
    for _ in range(100):
        radar._adapt_interval(conn, {"action": "check", "requests": 3})
    assert radar.current_interval(conn) == cfg.RADAR_MIN_INTERVAL_S
    monkeypatch.setattr(cfg, "RADAR_INTERVAL_S", 300)  # changed in Settings: start over
    assert radar.current_interval(conn) == 300


def test_empty_pairs_are_scanned_less_often(conn, us_only, monkeypatch):
    monkeypatch.setattr(trends, "category_queries", lambda geo, cat: {"top": [], "rising": []})
    radar.tick(conn, now=NOW)
    scope = radar.get_scope(conn)
    assert radar._next_source(conn, scope, NOW + timedelta(days=2)) is None
    assert radar._next_source(conn, scope, NOW + timedelta(days=cfg.RADAR_EMPTY_RESCAN_DAYS, minutes=1)) == ("US", 66)


def test_barren_pairs_slow_down(conn):
    src = {"error": "", "n_found": 40, "scans": 3}
    assert radar._rescan_hours(src, passes=0) == cfg.RADAR_BARREN_RESCAN_DAYS * 24
    assert radar._rescan_hours(src, passes=1) == cfg.RADAR_RESCAN_HOURS
    assert radar._rescan_hours({**src, "scans": 1}, passes=0) == cfg.RADAR_RESCAN_HOURS


def test_entity_check_shared_across_markets(conn, fake):
    radar.save_scope(conn, ["US", "CA"], [66], rd.DEFAULT_BLOCKLIST, all_markets=False)
    t = NOW
    for _ in range(6):
        radar.tick(conn, now=t, force=True)
        t += timedelta(minutes=10)
    assert fake.count(("entity", "katseye")) == 1  # CA reuses the US result
    assert {k["region"] for k in radar.keywords(conn, [rd.EXCLUDED]) if k["keyword"] == "katseye"} == {"US", "CA"}


def test_time_to_max_pace(conn, monkeypatch):
    monkeypatch.setattr(cfg, "RADAR_INTERVAL_S", 240)
    monkeypatch.setattr(cfg, "RADAR_SPEEDUP_EVERY", 10)
    monkeypatch.setattr(cfg, "RADAR_SPEEDUP_PCT", 20)
    monkeypatch.setattr(cfg, "RADAR_MIN_INTERVAL_S", 60)
    # 240 → 192 → 153.6 → 122.9 → 98.3 → 78.6 → 62.9 → 60: 7 steps × 10 ticks
    expected = 10 * (240 + 192 + 153.6 + 122.88 + 98.304 + 78.6432 + 62.91456) / 3600
    assert radar.time_to_max_pace(conn) == pytest.approx(expected)
    s = radar.stats(conn)
    assert s["max_jobs_per_day"] == 1440 and s["hours_to_max"] == pytest.approx(expected, abs=0.05)


def test_trends_errors_do_not_block_queue(conn, us_only, monkeypatch):
    def entity_404(q):
        raise RuntimeError("The request failed: Google returned a response with code 404")

    monkeypatch.setattr(trends, "entity_types", entity_404)
    radar.tick(conn, now=NOW)  # scan: 2 pending
    r = radar.tick(conn, now=NOW + timedelta(minutes=10))
    assert r["action"] == "check"  # entity lookup error: the shape is still judged, nothing stops
    assert {k["keyword"] for k in radar.keywords(conn, [rd.PASSED, rd.EXCLUDED])}

    def series_404(q, geo, source="web"):
        raise RuntimeError("code 404")

    monkeypatch.setattr(trends, "series_30d", series_404)
    radar.tick(conn, now=NOW + timedelta(minutes=20))
    assert radar.keywords(conn, [rd.PENDING]) == []  # 30-day series error: excluded with a reason, the queue is not stuck
    assert any("error while fetching 30 days" in k["reason"] for k in radar.keywords(conn, [rd.EXCLUDED]))


def test_youtube_empty_excluded_without_web_call(conn, us_only, monkeypatch):
    calls = []
    monkeypatch.setattr(trends, "series_30d", lambda q, geo, source="web": calls.append(source) or [])
    t = NOW
    for _ in range(3):
        radar.tick(conn, now=t)
        t += timedelta(minutes=10)
    out = {k["keyword"]: k for k in radar.keywords(conn, [rd.EXCLUDED])}
    assert out["dog training tips"]["reason"].startswith(f"{rd.SOURCE_LABEL['youtube']}:")
    assert out["dog training tips"]["trend"]["volume"]["youtube"]["avg"] == 0  # no YouTube data at all
    assert calls == ["youtube"]  # YouTube empty: excluded right away, Google Search never called


def test_failed_tick_still_schedules_next(conn, us_only, monkeypatch):
    def crash(geo, cat):
        raise KeyError("boom")

    monkeypatch.setattr(radar, "_run_trends_job", lambda conn_, now: crash(None, None))
    with pytest.raises(KeyError):
        radar.tick(conn, now=NOW)
    assert radar.next_due(conn) > NOW  # a failed tick still schedules the next one, no tight retry loop


def test_pace_log_and_report(conn, us_only, monkeypatch):
    t = NOW
    for _ in range(4):
        radar.tick(conn, now=t, force=True)
        t += timedelta(minutes=5)
    rows = radar.pace_log(conn, 1, now=t)
    assert rows and all(r["requests"] > 0 and r["forced"] == 1 for r in rows)
    assert rows[1]["gap_s"] == 300 and rows[-1]["req_1h"] == sum(r["requests"] for r in rows)
    assert radar.pace_report(conn, 1, now=t)["suggested_min_interval"] is None  # never blocked yet

    def blocked(q, geo, source="web"):
        raise trends.Throttled()

    monkeypatch.setattr(trends, "series_30d", blocked)
    monkeypatch.setattr(trends, "category_queries", lambda geo, cat: (_ for _ in ()).throw(trends.Throttled()))
    conn.execute("UPDATE radar_source SET scanned_at = ?", ("2000-01-01T00:00:00+00:00",))  # pair due: next tick calls Google
    radar.tick(conn, now=t, force=True)
    rep = radar.pace_report(conn, 1, now=t)
    assert rep["blocks"] == 1 and rep["min_block_req_1h"] == rows[-1]["req_1h"]
    last = radar.pace_log(conn, 1, now=t)[-1]
    assert last["throttled"] == 1 and last["interval_after"] == pytest.approx(last["interval_before"] * 2)
    assert rep["suggested_min_interval"] == max(30, round(3600 * rep["req_per_tick"] / (0.8 * rep["min_block_req_1h"])))


def test_rescued_429_is_logged(conn, us_only, monkeypatch):
    real = trends.category_queries

    def flaky(geo, cat):
        trends.stats["rescued"] += 1  # as when a 429 is followed by a successful new session
        return real(geo, cat)

    monkeypatch.setattr(trends, "category_queries", flaky)
    r = radar.tick(conn, now=NOW)
    assert r["rescued"] == 1 and "a fresh cookie got through" in r["detail"]
    assert radar.pace_report(conn, 1, NOW + timedelta(minutes=1))["rescued"] == 1
