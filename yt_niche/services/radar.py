"""Keyword radar: each tick does ONE small job against Google Trends, with ticks spread over 24 hours to avoid 429s.

A tick's job (keywords waiting to be checked go first, so the queue does not grow):
- check one new keyword (1–3 requests): short-lived entity filter, then the 5-year shape;
- scan one market × category pair (2 requests): fetch top / rising queries, add at most RADAR_PER_SOURCE new keywords.
Then, if the day's YouTube quota budget allows, YouTube-score the highest-scoring passed keyword.

tick() is called from Windows Task Scheduler (scripts/radar_tick.py) or from a background thread while the app is open;
a lock in the DB and the "next due" timestamp make sure two callers never double the number of requests.
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

from yt_niche import config as cfg
from yt_niche.core import radar as rd
from yt_niche.db import cache
from yt_niche.services import ai
from yt_niche.services.market_catalog import apply as apply_markets
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import trends
from yt_niche.sources.youtube import QuotaExceeded

ENTITY_TTL = 90 * 86400
TASK_NAME = "YT-NICHE Radar"
TASK_EVERY_MINUTES = 2  # Task Scheduler calls every 2 minutes; tick skips itself when it is not due yet
LOCK_SECONDS = 600


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# State (radar_meta table)
# ---------------------------------------------------------------------------


def meta_get(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM radar_meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def meta_set(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO radar_meta (key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


def get_scope(conn: sqlite3.Connection) -> dict:
    """Scan scope: markets, categories, blocklist. Default: every market, Google's evergreen categories."""
    scope = meta_get(conn, "scope") or {}
    markets = [m for m in scope.get("markets", cfg.ENABLED_MARKETS) if m in cfg.ENABLED_MARKETS]
    categories = scope.get("categories")
    if categories is None:
        categories = rd.default_categories(trends.category_tree(conn))
    return {
        "markets": markets,
        "categories": [c for c in categories if c not in rd.EXCLUDED_CATEGORIES],
        "blocklist": scope.get("blocklist", rd.DEFAULT_BLOCKLIST),
        "all_markets": scope.get("all_markets", "markets" not in scope),
    }


def save_scope(conn: sqlite3.Connection, markets: list[str], categories: list[int], blocklist: list[str], all_markets: bool) -> None:
    """all_markets=True: markets added later are scanned automatically too."""
    meta_set(conn, "scope", {
        "markets": list(cfg.ENABLED_MARKETS) if all_markets else markets,
        "all_markets": all_markets,
        "categories": [int(c) for c in categories],
        "blocklist": [b.strip() for b in blocklist if b.strip()],
    })


def _markets(conn: sqlite3.Connection, scope: dict) -> list[str]:
    return list(cfg.ENABLED_MARKETS) if scope["all_markets"] else scope["markets"]


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------


def _acquire(conn: sqlite3.Connection, now: datetime) -> bool:
    conn.execute("INSERT OR IGNORE INTO radar_meta (key, value) VALUES ('lock_until', '0')")
    cur = conn.execute(
        "UPDATE radar_meta SET value = ? WHERE key = 'lock_until' AND CAST(value AS REAL) < ?",
        (json.dumps((now + timedelta(seconds=LOCK_SECONDS)).timestamp()), now.timestamp()),
    )
    return cur.rowcount == 1


def _release(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE radar_meta SET value = '0' WHERE key = 'lock_until'")


def next_due(conn: sqlite3.Connection) -> datetime | None:
    v = meta_get(conn, "next_due_at")
    return datetime.fromisoformat(v) if v else None


def tick(conn: sqlite3.Connection, now: datetime | None = None, force: bool = False) -> dict:
    """Run one tick if it is due. Returns {"action", "detail"}; action="skip" when not due yet / another tick is running."""
    now = now or _now()
    apply_markets(conn)
    due = next_due(conn)
    if not force and due and now < due:
        return {"action": "skip", "detail": f"next tick at {due.astimezone():%H:%M}"}
    if not _acquire(conn, now):
        return {"action": "skip", "detail": "another tick is running"}
    try:
        meta_set(conn, "next_due_at", _iso(now + timedelta(seconds=current_interval(conn) * random.uniform(0.8, 1.2))))
        result = {"action": "idle", "detail": "", "requests": 0}
        details = []
        ai_note = _ai_step(conn, now)  # before Trends: short-lived keywords are dropped early, no Google requests spent
        trends.stats["rescued"] = 0
        for _ in range(MAX_FREE_JOBS):  # jobs that do not call Google (already cached) continue right away in this tick
            result = _run_trends_job(conn, now)
            details.append(result["detail"])
            if result.get("requests") or result.get("paid") or result["action"] not in ("discover", "check"):
                break
        result["detail"] = " · ".join(([ai_note] if ai_note else []) + details)
        if rescued := trends.stats["rescued"]:
            result["rescued"] = rescued
            result["detail"] += f" · Google blocked {rescued} request(s); a fresh cookie got through"
        interval_before = current_interval(conn)
        _adapt_interval(conn, result)
        _log_pace(conn, now, result, interval_before, force)
        jitter = random.uniform(0.8, 1.2)
        meta_set(conn, "next_due_at", _iso(now + timedelta(seconds=current_interval(conn) * jitter)))
        yt = _score_youtube(conn, now)
        if yt:
            result["detail"] += f" · {yt}"
        log = meta_get(conn, "log", [])[-49:] + [{"at": _iso(now), **result}]
        meta_set(conn, "log", log)
        day = now.astimezone().date().isoformat()
        if result.get("requests"):
            meta_set(conn, f"jobs:{day}", meta_get(conn, f"jobs:{day}", 0) + 1)
        return result
    finally:
        _release(conn)


MAX_FREE_JOBS = 10


def _ai_step(conn: sqlite3.Connection, now: datetime, keys: list[tuple[str, str]] | None = None) -> str | None:
    """AI classification for `keys` (region, keyword) just found, or when not given: up to ai.RADAR_BATCH leftover
    unclassified keywords (queue first). An AI error never blocks the Radar."""
    if not ai.enabled(conn, "radar"):
        return None
    if keys is not None:
        rows = [
            r for k in keys
            if (r := conn.execute("SELECT region, keyword, status FROM radar_keyword WHERE region = ? AND keyword = ?", k).fetchone())
        ]
    else:
        markets = cfg.ENABLED_MARKETS
        rows = conn.execute(
            f"""SELECT region, keyword, status FROM radar_keyword
                WHERE ai_type IS NULL AND status IN ('pending', 'passed', 'watching') AND region IN ({','.join('?' * len(markets))})
                ORDER BY status = 'pending' DESC, potential IS NULL, potential DESC, value DESC LIMIT ?""",
            [*markets, ai.RADAR_BATCH],
        ).fetchall()
    if not rows:
        return None
    try:
        got = ai.classify_keywords(conn, [r["keyword"] for r in rows])
    except Exception as e:  # whatever the AI error, the Radar goes on
        return f"AI error: {str(e)[:80] or type(e).__name__}"
    dropped = 0
    for r in rows:
        res = got.get(r["keyword"])
        if res is None:
            continue
        conn.execute(
            "UPDATE radar_keyword SET ai_type = ?, ai_vi = ? WHERE region = ? AND keyword = ?",
            (res["type"], res["vi"], r["region"], r["keyword"]),
        )
        if res["short"] and r["status"] == rd.PENDING:
            reason = f"AI: {res['label']}" + (f" ({res['why']})" if res["why"] else "") + " – short-lived trend"
            _set_checked(conn, r["region"], r["keyword"], now, rd.EXCLUDED, reason, None, None)
            dropped += 1
    return f"AI classified {len(got)} keywords, dropped {dropped} short-lived"


def current_interval(conn: sqlite3.Connection) -> float:
    """Current gap between ticks (seconds). Changing RADAR_INTERVAL_S in Settings restarts from that value."""
    state = meta_get(conn, "pace") or {}
    if state.get("base") != cfg.RADAR_INTERVAL_S:
        return float(cfg.RADAR_INTERVAL_S)
    return min(cfg.RADAR_MAX_INTERVAL_S, max(cfg.RADAR_MIN_INTERVAL_S, state["interval"]))


def _speedup_factor() -> float:
    return 1 - min(90, max(1, cfg.RADAR_SPEEDUP_PCT)) / 100


def time_to_max_pace(conn: sqlite3.Connection) -> float:
    """Hours until the fastest pace is reached if Google never blocks (0 if already there)."""
    interval = current_interval(conn)
    state = meta_get(conn, "pace") or {}
    left = cfg.RADAR_SPEEDUP_EVERY - (state.get("streak", 0) if state.get("base") == cfg.RADAR_INTERVAL_S else 0)
    seconds = 0.0
    while interval > cfg.RADAR_MIN_INTERVAL_S:
        seconds += max(0, left) * interval
        interval, left = max(cfg.RADAR_MIN_INTERVAL_S, interval * _speedup_factor()), cfg.RADAR_SPEEDUP_EVERY
    return seconds / 3600


def _adapt_interval(conn: sqlite3.Connection, result: dict) -> None:
    """Speed up gradually while Google does not block, slow down twofold when it does."""
    state = meta_get(conn, "pace") or {}
    interval = current_interval(conn)
    streak = state.get("streak", 0) if state.get("base") == cfg.RADAR_INTERVAL_S else 0
    if result.get("throttled"):
        interval, streak = min(cfg.RADAR_MAX_INTERVAL_S, interval * 2), 0
    elif result.get("requests"):
        streak += 1
        if streak >= cfg.RADAR_SPEEDUP_EVERY:
            interval, streak = max(cfg.RADAR_MIN_INTERVAL_S, interval * _speedup_factor()), 0
    meta_set(conn, "pace", {"base": cfg.RADAR_INTERVAL_S, "interval": round(interval, 1), "streak": streak})
    if result.get("requests"):
        counts = meta_get(conn, "job_counts") or {"discover": 0, "check": 0}
        counts[result["action"]] = counts.get(result["action"], 0) + 1
        meta_set(conn, "job_counts", counts)


PACE_LOG_DAYS = 180


def _log_pace(conn: sqlite3.Connection, now: datetime, result: dict, interval_before: float, forced: bool) -> None:
    """Write one pace-log row (idle ticks that did not call Google are skipped)."""
    if not result.get("requests") and not result.get("throttled") and not result.get("rescued"):
        return
    ts = _iso(now)
    prev = conn.execute("SELECT at FROM radar_pace_log WHERE requests > 0 AND at < ? ORDER BY at DESC LIMIT 1", (ts,)).fetchone()
    gap = (now - datetime.fromisoformat(prev["at"])).total_seconds() if prev else None
    last_hour = conn.execute(
        "SELECT COALESCE(SUM(requests), 0) FROM radar_pace_log WHERE at > ? AND at <= ?", (_iso(now - timedelta(hours=1)), ts)
    ).fetchone()[0]
    pace = meta_get(conn, "pace") or {}
    conn.execute(
        """INSERT INTO radar_pace_log (at, action, requests, throttled, interval_before, interval_after, streak, gap_s, req_1h,
               forced, rescued) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, result["action"], int(result.get("requests") or 0), int(bool(result.get("throttled"))), round(interval_before, 1),
         round(current_interval(conn), 1), pace.get("streak", 0), round(gap, 1) if gap is not None else None,
         last_hour + int(result.get("requests") or 0), int(forced), int(result.get("rescued") or 0)),
    )
    conn.execute("DELETE FROM radar_pace_log WHERE at < ?", (_iso(now - timedelta(days=PACE_LOG_DAYS)),))


def pace_log(conn: sqlite3.Connection, days: float, now: datetime | None = None) -> list[dict]:
    since = _iso((now or _now()) - timedelta(days=days))
    return [dict(r) for r in conn.execute("SELECT * FROM radar_pace_log WHERE at >= ? ORDER BY at", (since,))]


def pace_report(conn: sqlite3.Connection, days: float = 7, now: datetime | None = None) -> dict:
    """Summary for tuning the pace: at how many requests/hour Google blocks, which pace ran safely, suggested fastest pace."""
    rows = pace_log(conn, days, now)
    blocks = [r for r in rows if r["throttled"]]
    ok = [r for r in rows if not r["throttled"] and r["requests"]]
    req_per_tick = sum(r["requests"] for r in ok) / len(ok) if ok else 1 + cfg.RADAR_PER_SOURCE
    gaps = sorted(r["gap_s"] for r in ok if r["gap_s"])
    out = {
        "rows": rows,
        "ticks": len(rows),
        "requests": sum(r["requests"] for r in rows),
        "blocks": len(blocks),
        "rescued": sum(r.get("rescued") or 0 for r in rows),
        "req_per_tick": round(req_per_tick, 2),
        "median_gap_s": round(gaps[len(gaps) // 2]) if gaps else None,
        "fastest_ok_interval": min((r["interval_before"] for r in ok), default=None),
        "max_ok_req_1h": max((r["req_1h"] for r in ok), default=0),
        "min_block_req_1h": min((r["req_1h"] for r in blocks), default=None),
        "suggested_min_interval": None,
    }
    if blocks:
        # Stay below 80% of the lowest requests/hour that ever got blocked.
        safe_per_hour = max(1.0, 0.8 * out["min_block_req_1h"])
        out["suggested_min_interval"] = max(30, round(3600 * req_per_tick / safe_per_hour))
    return out


GOOGLE, BACKUP = "google", "dataforseo"


def _run_trends_job(conn: sqlite3.Connection, now: datetime) -> dict:
    """One Trends job. While Google blocks, if DataForSEO is enabled for the Radar, run it through DataForSEO (paid, daily cap)."""
    via = GOOGLE
    if until := trends.cooldown_until(conn):
        if dfs.unavailable_reason(conn, "radar"):
            return {"action": "cooldown", "detail": f"Google is blocking for now, resting until {until:%H:%M}"}
        via = BACKUP
    try:
        scope = get_scope(conn)
    except trends.Throttled:
        trends.start_cooldown(conn)
        return {"action": "cooldown", "detail": "Google blocked while fetching the category tree", "throttled": True}
    try:
        return _job(conn, scope, now, via)
    except trends.Throttled:
        trends.start_cooldown(conn)
        note = f"{trends.THROTTLED}: resting {trends.COOLDOWN_SECONDS // 60} min, slowing down twofold"
        if dfs.unavailable_reason(conn, "radar"):
            return {"action": "cooldown", "throttled": True, "detail": note}
        try:
            res = _job(conn, scope, now, BACKUP)
        except dfs.BackupError as e:
            return {"action": "cooldown", "throttled": True, "detail": f"{note}; DataForSEO error: {e}"}
        return {**res, "throttled": True, "detail": f"{note} → {res['detail']}"}
    except dfs.BackupError as e:
        return {"action": "cooldown", "detail": f"Google is blocking for now; DataForSEO error: {e}"}


def _job(conn: sqlite3.Connection, scope: dict, now: datetime, via: str) -> dict:
    if pending := _next_pending(conn):
        return check_keyword(conn, pending, now, via)
    if source := _next_source(conn, scope, now):
        return discover(conn, *source, scope["blocklist"], now, via)
    return {"action": "idle", "detail": "no pair in scope is due for a rescan yet"}


def _cost(via: str, calls: int = 1) -> dict:
    """Each Google call: 2 requests (explore + data) counted against the pace. DataForSEO: 1 paid call, not counted against Google's pace."""
    return {"requests": 2 * calls} if via == GOOGLE else {"requests": 0, "paid": calls}


def _via_note(via: str) -> str:
    return " [DataForSEO]" if via == BACKUP else ""


def _rescan_hours(src: dict | None, passes: int) -> float:
    """Never scanned: now. No data: 7 days. Scanned 3+ times without a passed keyword: 3 days. Otherwise: 24 hours."""
    if src is None:
        return 0
    if src["error"] or not src["n_found"]:
        return cfg.RADAR_EMPTY_RESCAN_DAYS * 24
    if src["scans"] >= 3 and not passes:
        return cfg.RADAR_BARREN_RESCAN_DAYS * 24
    return cfg.RADAR_RESCAN_HOURS


def _pair_state(conn: sqlite3.Connection, scope: dict) -> list[dict]:
    markets, cats = _markets(conn, scope), scope["categories"]
    sources = {(r["region"], r["category_id"]): dict(r) for r in conn.execute("SELECT * FROM radar_source")}
    passes = {
        (r["region"], r["category_id"]): r["n"]
        for r in conn.execute(
            """SELECT region, category_id, COUNT(*) AS n FROM radar_keyword
               WHERE status IN ('passed', 'watching') GROUP BY region, category_id"""
        )
    }
    # Interleave markets: category 1 of every market, then category 2...
    return [
        {"pair": (m, c), "scanned_at": (sources.get((m, c)) or {}).get("scanned_at"),
         "hours": _rescan_hours(sources.get((m, c)), passes.get((m, c), 0))}
        for c in cats for m in markets
    ]


def _next_source(conn: sqlite3.Connection, scope: dict, now: datetime) -> tuple[str, int] | None:
    """The pair that has been due the longest (never scanned goes first)."""
    due = [
        p for p in _pair_state(conn, scope)
        if p["scanned_at"] is None or datetime.fromisoformat(p["scanned_at"]) <= now - timedelta(hours=p["hours"])
    ]
    return min(due, key=lambda p: p["scanned_at"] or "")["pair"] if due else None


def _next_pending(conn: sqlite3.Connection) -> dict | None:
    """Keyword waiting to be checked in an enabled market (disabled markets are skipped, no requests spent)."""
    markets = cfg.ENABLED_MARKETS
    row = conn.execute(
        f"""SELECT * FROM radar_keyword WHERE status = 'pending' AND region IN ({','.join('?' * len(markets))})
            ORDER BY kind = 'rising' DESC, value DESC, first_seen LIMIT 1""",
        markets,
    ).fetchone()
    return dict(row) if row else None


def discover(
    conn: sqlite3.Connection, region: str, category_id: int, blocklist: list[str], now: datetime, via: str = GOOGLE
) -> dict:
    """Scan one category in one market: update known keywords, add at most RADAR_PER_SOURCE new ones."""
    try:
        if via == BACKUP:
            found = dfs.category_queries(conn, "radar", region, category_id)
        else:
            found = trends.category_queries(region, category_id)
    except (trends.Throttled, dfs.BackupError):
        raise
    except Exception as e:  # other errors (category has no data in this market...): record and move on
        _save_source(conn, region, category_id, now, 0, f"{type(e).__name__}: {str(e)[:150]}")
        return {"action": "discover", **_cost(via),
                "detail": f"{region} · category {category_id}: error {type(e).__name__}{_via_note(via)}"}

    day, ts = now.astimezone().date().isoformat(), _iso(now)
    known = {rd.fold(r["keyword"]): dict(r) for r in conn.execute("SELECT * FROM radar_keyword WHERE region = ?", (region,))}
    recheck_before = _iso(now - timedelta(days=cfg.RADAR_RECHECK_DAYS))
    spike_before = _iso(now - timedelta(days=cfg.RADAR_SPIKE_RECHECK_DAYS))
    seen_again = set()
    for kind in ("rising", "top"):
        for q, v in found.get(kind, []):
            key = rd.fold(q)
            if (row := known.get(key)) is None or key in seen_again:
                continue  # present in both lists: count it once
            seen_again.add(key)
            new_day = datetime.fromisoformat(row["last_seen"]).astimezone().date().isoformat() != day
            status = row["status"]
            if status == rd.PASSED and (row["checked_at"] or "") < recheck_before:
                status = rd.PENDING  # passed long ago: re-check the shape
            elif status == rd.EXCLUDED and '"retry": true' in (row["trend_json"] or "") and (row["checked_at"] or "") < spike_before:
                status = rd.PENDING  # spiked last time but still showing up: see whether it became a lasting trend
            conn.execute(
                """UPDATE radar_keyword SET last_seen = ?, seen_days = seen_days + ?, value = MAX(value, ?),
                       status = ? WHERE region = ? AND keyword = ?""",
                (ts, int(new_day), v if kind == row["kind"] else 0, status, region, row["keyword"]),
            )
            _refresh_potential(conn, region, row["keyword"])
    new = rd.pick_new(found, set(known), blocklist, cfg.RADAR_PER_SOURCE)
    for k in new:
        conn.execute(
            """INSERT INTO radar_keyword (region, keyword, category_id, kind, value, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (region, k["keyword"], category_id, k["kind"], k["value"], ts, ts),
        )
    n = len(found.get("rising", [])) + len(found.get("top", []))
    _save_source(conn, region, category_id, now, n, "")
    detail = f"{region} · category {category_id}: {n} queries, {len(new)} new, {len(seen_again)} seen again{_via_note(via)}"
    # Classify with AI as soon as there are new keywords (meaning, type; short-lived ones are dropped right away, no Google requests).
    if new and (ai_note := _ai_step(conn, now, [(region, k["keyword"]) for k in new])):
        detail += f" · {ai_note}"
    return {"action": "discover", **_cost(via), "detail": detail}


def _save_source(conn: sqlite3.Connection, region: str, category_id: int, now: datetime, n: int, error: str) -> None:
    conn.execute(
        """INSERT INTO radar_source (region, category_id, scanned_at, n_found, error, scans) VALUES (?, ?, ?, ?, ?, 1)
           ON CONFLICT(region, category_id) DO UPDATE SET scanned_at = excluded.scanned_at, n_found = excluded.n_found,
               error = excluded.error, scans = scans + 1""",
        (region, category_id, _iso(now), n, error),
    )


def check_keyword(conn: sqlite3.Connection, row: dict, now: datetime, via: str = GOOGLE) -> dict:
    """Drop short-lived entities, then judge the 30-day search volume on YouTube Search and Google Search. Record passed / excluded."""
    region, kw = row["region"], row["keyword"]
    # Entity types are global to Google, not per market: check once, other markets reuse it.
    # Entity suggestions (autocomplete) are not rate-limited like Trends data: not counted against the pace.
    key = cache.make_key("radar_entity", {"q": rd.fold(kw)})
    if (entities := cache.get(conn, key)) is None:
        try:
            entities = trends.entity_types(kw)
            cache.put(conn, key, entities, ENTITY_TTL)
        except trends.Throttled:
            if via == GOOGLE:
                raise
            entities = []  # on DataForSEO: skip this step, judge the volume as usual
        except Exception:
            entities = []
    hit = rd.short_term_entity(kw, entities)
    entity_type = hit["type"] if hit else (entities[0]["type"] if entities else None)
    if hit:
        _set_checked(conn, region, kw, now, rd.EXCLUDED, f"{hit['title']} is a {hit['type']} (short-lived trend)", entity_type, None)
        return {"action": "check", "requests": 0, "detail": f"{region} · \"{kw}\": excluded ({hit['type']})"}
    volume = {}
    for source in ("youtube", "web"):  # YouTube first: sparser, so if it is missing exclude right away and skip Google Search
        try:
            if via == BACKUP:
                points = dfs.series(conn, "radar", [kw], region, source, "1m")[kw]
            else:
                points = trends.series_30d(kw, region, source)
        except (trends.Throttled, dfs.BackupError):
            raise
        except Exception as e:
            reason = f"Google Trends error while fetching 30 days ({rd.SOURCE_LABEL[source]}): {type(e).__name__} {str(e)[:120]}"
            _set_checked(conn, region, kw, now, rd.EXCLUDED, reason, entity_type, None)
            return {"action": "check", **_cost(via, len(volume) + 1),
                    "detail": f"{region} · \"{kw}\": excluded ({reason[:60]}){_via_note(via)}"}
        volume[source] = rd.volume_30d(points)
        if not volume[source]["ok"]:
            break
    verdict = rd.judge_volume(volume)
    status = rd.PASSED if verdict["ok"] else rd.EXCLUDED
    _set_checked(conn, region, kw, now, status, verdict["reason"], entity_type, verdict)
    return {"action": "check", **_cost(via, len(volume)),
            "detail": f"{region} · \"{kw}\": {rd.STATUS_LABEL[status]} ({verdict['reason'][:60]}){_via_note(via)}"}


def _set_checked(conn, region, kw, now, status, reason, entity_type, shape) -> None:
    conn.execute(
        """UPDATE radar_keyword SET status = ?, reason = ?, entity_type = ?, trend_class = ?, trend_json = ?,
               checked_at = ? WHERE region = ? AND keyword = ?""",
        (status, reason, entity_type, (shape or {}).get("class"), json.dumps(shape, ensure_ascii=False) if shape else None,
         _iso(now), region, kw),
    )
    _refresh_potential(conn, region, kw)


def _refresh_potential(conn: sqlite3.Connection, region: str, kw: str) -> None:
    row = conn.execute("SELECT * FROM radar_keyword WHERE region = ? AND keyword = ?", (region, kw)).fetchone()
    if row is None:
        return
    r = _row(row)
    conn.execute("UPDATE radar_keyword SET potential = ? WHERE region = ? AND keyword = ?", (rd.potential(r), region, kw))


# ---------------------------------------------------------------------------
# YouTube scoring (spends quota, daily cap)
# ---------------------------------------------------------------------------


def yt_units_today(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    return meta_get(conn, f"yt_units:{(now or _now()).astimezone().date().isoformat()}", 0)


def score_youtube(conn: sqlite3.Connection, region: str, keyword: str, now: datetime | None = None) -> int:
    """Score one keyword with the YouTube keyword score (≈102 units). Returns the units spent."""
    from yt_niche.services.keywords import _score_many

    now = now or _now()
    scored, units = asyncio.run(_score_many(conn, region, [keyword], False))
    s = scored[0]
    metrics = {k: s[k] for k in ("score", "demand", "competition", "n", "low_data", "median_views", "big_share", "exact_share", "label")}
    conn.execute(
        "UPDATE radar_keyword SET yt_score = ?, yt_json = ?, yt_scored_at = ? WHERE region = ? AND keyword = ?",
        (s["score"], json.dumps(metrics, ensure_ascii=False), _iso(now), region, keyword),
    )
    _refresh_potential(conn, region, keyword)
    key = f"yt_units:{now.astimezone().date().isoformat()}"
    meta_set(conn, key, meta_get(conn, key, 0) + units)
    return units


def _score_youtube(conn: sqlite3.Connection, now: datetime) -> str | None:
    if not cfg.api_key() or yt_units_today(conn, now) + 102 > cfg.RADAR_YT_UNITS_PER_DAY:
        return None
    row = conn.execute(
        """SELECT region, keyword FROM radar_keyword WHERE status IN ('passed', 'watching') AND yt_score IS NULL
           ORDER BY potential DESC LIMIT 1"""
    ).fetchone()
    if row is None or row["region"] not in cfg.ENABLED_MARKETS:
        return None
    try:
        units = score_youtube(conn, row["region"], row["keyword"], now)
    except QuotaExceeded:
        meta_set(conn, f"yt_units:{now.astimezone().date().isoformat()}", cfg.RADAR_YT_UNITS_PER_DAY)
        return "YouTube quota exhausted for today"
    except Exception as e:
        return f"YouTube scoring error: {type(e).__name__}"
    return f"YouTube-scored \"{row['keyword']}\" ({units} units)"


# ---------------------------------------------------------------------------
# Reading / actions for the UI
# ---------------------------------------------------------------------------


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["trend"] = json.loads(d.pop("trend_json") or "null")
    d["yt"] = json.loads(d.pop("yt_json") or "null")
    return d


def keywords(conn: sqlite3.Connection, statuses: list[str], regions: list[str] | None = None) -> list[dict]:
    q = f"SELECT * FROM radar_keyword WHERE status IN ({','.join('?' * len(statuses))})"
    args = list(statuses)
    if regions:
        q += f" AND region IN ({','.join('?' * len(regions))})"
        args += regions
    q += " ORDER BY potential IS NULL, potential DESC, value DESC"
    return [_row(r) for r in conn.execute(q, args)]


def set_status(conn: sqlite3.Connection, items: list[tuple[str, str]], status: str) -> None:
    for region, kw in items:
        conn.execute("UPDATE radar_keyword SET status = ? WHERE region = ? AND keyword = ?", (status, region, kw))


def new_good_count(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    since = _iso((now or _now()) - timedelta(hours=24))
    markets = cfg.ENABLED_MARKETS
    return conn.execute(
        f"""SELECT COUNT(*) FROM radar_keyword WHERE status = 'passed' AND potential >= ? AND first_seen >= ?
            AND region IN ({','.join('?' * len(markets))})""",
        (cfg.RADAR_GOOD_MIN, since, *markets),
    ).fetchone()[0]


def stats(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or _now()
    day = now.astimezone().date().isoformat()
    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM radar_keyword GROUP BY status")}
    try:
        state = _pair_state(conn, get_scope(conn))
    except Exception:  # 429, no network, proxy refused: stats are display-only, show what the DB has
        state = []
    interval = current_interval(conn)
    jobs_per_day = 86400 / interval
    # Ticks per pair = 1 scan + the checks it triggers, measured from real data (worst-case estimate until there is enough).
    jc = meta_get(conn, "job_counts") or {}
    per_pair = (jc.get("discover", 0) + jc.get("check", 0)) / jc["discover"] if jc.get("discover", 0) >= 5 else 1 + cfg.RADAR_PER_SOURCE
    # Pairs scanned rarely (7 days, 3 days) take only a fraction of the ticks per day.
    daily_pairs = sum(1 if not p["hours"] else min(1.0, 24 / p["hours"]) for p in state)
    unscanned = sum(p["scanned_at"] is None for p in state)
    cutoff = _iso(now - timedelta(hours=cfg.RADAR_RESCAN_HOURS))
    fresh = conn.execute("SELECT COUNT(*) FROM radar_source WHERE scanned_at >= ?", (cutoff,)).fetchone()[0]
    return {
        "counts": counts,
        "pairs": len(state) or None,
        "unscanned": unscanned,
        "slow_pairs": sum(1 for p in state if p["hours"] > cfg.RADAR_RESCAN_HOURS),
        "fresh_pairs": fresh,
        "per_pair": round(per_pair, 1),
        "interval_s": round(interval),
        "cycle_days": round(daily_pairs * per_pair / jobs_per_day, 1) if state else None,
        "first_sweep_days": round(unscanned * per_pair / jobs_per_day, 1),
        # At the fastest pace (if Google never blocks): RADAR_MIN_INTERVAL_S seconds per tick.
        "max_jobs_per_day": round(86400 / cfg.RADAR_MIN_INTERVAL_S),
        "cycle_days_at_max": round(daily_pairs * per_pair / (86400 / cfg.RADAR_MIN_INTERVAL_S), 1) if state else None,
        "hours_to_max": round(time_to_max_pace(conn), 1),
        "jobs_today": meta_get(conn, f"jobs:{day}", 0),
        "jobs_per_day": round(jobs_per_day),
        "yt_units_today": yt_units_today(conn, now),
        "next_due": next_due(conn),
        "log": list(reversed(meta_get(conn, "log", []))),
    }


# ---------------------------------------------------------------------------
# Windows background schedule (Task Scheduler)
# ---------------------------------------------------------------------------


def _task_command() -> str:
    pythonw = cfg.ROOT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    exe = pythonw if pythonw.exists() else sys.executable
    return f'"{exe}" "{cfg.ROOT_DIR / "scripts" / "radar_tick.py"}"'


def task_status() -> bool | None:
    """True: enabled; False: not yet; None: not Windows."""
    if sys.platform != "win32":
        return None
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True, text=True)
    return r.returncode == 0


def enable_task() -> str:
    r = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _task_command(), "/SC", "MINUTE",
         "/MO", str(TASK_EVERY_MINUTES), "/F"],
        capture_output=True, text=True,
    )
    return (r.stdout or r.stderr).strip()


def disable_task() -> str:
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()
