"""Google Trends through pytrends-modern (the maintained pytrends fork, HTTP mode), with a SQLite cache.

Google returns 429 when a request has no good NID cookie (measured 10/2026: no cookie 3/3 times 429, fresh cookie 3/3
pass, one session keeping its cookie passed 7 calls in a row), and when one IP calls too often. Therefore:
- the same browser User-Agent for fetching the cookie and for fetching data;
- on 429 drop the cookie, open a new session (new cookie) and retry;
- cache each time range separately, next time fetch only what is missing;
- still 429 after 2 such retries: stop and rest for COOLDOWN_SECONDS, during which Google is not called;
- only fetch the 5-year series; 12 months and 3 months are derived from its tail (12 months is fetched separately only
  when the scale is too coarse);
- while Google blocks and DataForSEO is enabled (Settings → Google Trends), fetch through DataForSEO (paid per task).
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from pytrends_modern.request import TrendReq

from yt_niche.config import TTL_TRENDS
from yt_niche.db import cache
from yt_niche.sources import dataforseo as dfs

TIMEFRAMES = {"5y": "today 5-y", "12m": "today 12-m", "3m": "today 3-m"}  # 5 years first: the most important
TIMEFRAME_LABELS = {"5y": "5 years", "12m": "12 months", "3m": "3 months"}
SOURCES = {"web": "", "youtube": "youtube"}
TIMEFRAME_30D = "today 1-m"  # Radar: last 30 days, daily
EXPLORE_URL = "https://trends.google.com/trends/explore"
PAUSE_SECONDS = 1.5
RETRY_WAITS = (5, 20)  # seconds to wait before each retry (with a new session + cookie) after a 429
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# Number of 429s rescued by a fresh cookie (read and reset by Radar to log the scan pace).
stats = {"rescued": 0}
COOLDOWN_SECONDS = 600
COOLDOWN_KEY = "trends_cooldown:ip"
THROTTLED = "Google is temporarily blocking for too many requests (429)"
SKIPPED = "skipped because Google is temporarily blocking"
BACKUP_FAILED = "DataForSEO error"
PAUSED = "Trends calls paused until"  # follows THROTTLED after ", " in cooldown errors
DERIVE_DAYS = {"12m": 365, "3m": 91}  # ranges derived from the 5-year series (weekly)
LOW_RES_PEAK = 25  # last-52-week peak of the 5-year series below this: scale too coarse, fetch 12 months separately


def _is_429(e: Exception) -> bool:
    return "429" in str(e) or type(e).__name__ == "TooManyRequestsError"


def explore_url(keyword: str, geo: str, source: str = "web", timeframe: str = TIMEFRAMES["5y"]) -> str:
    """Google Trends page link (opens in the browser) for a keyword, market and source."""
    params = {"q": keyword, "geo": geo, "date": timeframe}
    if SOURCES[source]:
        params["gprop"] = SOURCES[source]
    return f"{EXPLORE_URL}?{urlencode(params)}"


def _client(hl: str) -> TrendReq:
    """New session: loads the Trends home page for a fresh NID cookie. HTTP mode (no browser, no Google sign-in).

    One fixed User-Agent for both the cookie fetch and the data calls (changing the UA midway looks like a bot).
    The library's retries/backoff are off: it retries with the old cookie, which is useless; the app retries with a new session.
    """
    return TrendReq(hl=hl, tz=-420, timeout=(10, 30), retries=0, backoff_factor=0, rotate_user_agent=False,
                    requests_args={"headers": {"User-Agent": USER_AGENT}})


def _fetch_many(topics: list[str], timeframes: dict[str, str], geo: str, source: str, hl: str) -> dict:
    """Call pytrends (synchronous). {"series": {range: {topic: [[date, value]]}}, "errors": {...}, "throttled": bool}."""
    series: dict[str, dict[str, list]] = {}
    errors: dict[str, str] = {}
    names = list(timeframes)
    try:
        client = _client(hl)
    except Exception as e:
        throttled = _is_429(e)
        return {"series": {}, "errors": {n: THROTTLED if throttled else str(e)[:200] for n in names}, "throttled": throttled}
    for i, name in enumerate(names):
        if i:
            time.sleep(PAUSE_SECONDS)
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                client.build_payload(topics, timeframe=timeframes[name], geo=geo, gprop=SOURCES[source])
                df = client.interest_over_time()
                series[name] = {
                    t: [] if df.empty or t not in df else [[idx.strftime("%Y-%m-%d"), int(v)] for idx, v in df[t].items()]
                    for t in topics
                }
                stats["rescued"] += bool(attempt)
                break
            except Exception as e:
                if _is_429(e) and attempt < len(RETRY_WAITS):
                    time.sleep(RETRY_WAITS[attempt])
                    try:
                        client = _client(hl)  # the old cookie may be flagged: get a new one
                    except Exception:
                        pass
                    continue
                if _is_429(e):
                    errors[name] = THROTTLED
                    errors.update({rest: SKIPPED for rest in names[i + 1:]})
                    return {"series": series, "errors": errors, "throttled": True}
                errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
                break
    return {"series": series, "errors": errors, "throttled": False}


# ---------------------------------------------------------------------------
# Cooldown after a block
# ---------------------------------------------------------------------------


def cooldown_until(conn: sqlite3.Connection) -> datetime | None:
    """When the cooldown ends (local time), or None when Google may be called."""
    until = cache.get(conn, COOLDOWN_KEY)
    return datetime.fromtimestamp(until) if until and until > time.time() else None


def _start_cooldown(conn: sqlite3.Connection) -> None:
    cache.put(conn, COOLDOWN_KEY, time.time() + COOLDOWN_SECONDS, COOLDOWN_SECONDS)


def _cooldown_errors(conn: sqlite3.Connection, names) -> dict[str, str] | None:
    if until := cooldown_until(conn):
        return {n: f"{THROTTLED}, {PAUSED} {until:%H:%M}" for n in names}
    return None


def describe_errors(errors: dict[str, str] | None) -> str | None:
    """A readable error sentence for the UI."""
    if not errors:
        return None
    missing = ", ".join(TIMEFRAME_LABELS.get(n, n) for n in errors)
    backup = next((v.split(f"{BACKUP_FAILED}: ", 1)[1] for v in errors.values() if f"{BACKUP_FAILED}: " in v), None)
    if backup:
        return f"Missing Trends {missing}: Google is temporarily blocking your machine and the DataForSEO backup failed too ({backup})."
    if any(THROTTLED in v or SKIPPED in v for v in errors.values()):
        tail = next((v.split(", ", 1)[1] for v in errors.values() if f", {PAUSED}" in v), None)
        wait = f" ({tail})" if tail else f" (Trends calls paused for {COOLDOWN_SECONDS // 60} minutes)"
        return (
            f"Missing Trends {missing}: Google is temporarily blocking your machine for too many requests{wait}. "
            "This usually clears on its own after 10–60 minutes; then click \"Refetch Google Trends\" (no YouTube quota)."
        )
    return f"Missing Trends {missing}: " + "; ".join(f"{TIMEFRAME_LABELS.get(n, n)}: {v}" for n, v in errors.items())


# ---------------------------------------------------------------------------
# Derive 12 months from 5 years
# ---------------------------------------------------------------------------


def _window(by_topic_5y: dict[str, list], days: int) -> dict[str, list]:
    last = max((date.fromisoformat(pts[-1][0]) for pts in by_topic_5y.values() if pts), default=None)
    if last is None:
        return {t: [] for t in by_topic_5y}
    start = (last - timedelta(days=days)).isoformat()
    return {t: [[d, v] for d, v in pts if d > start] for t, pts in by_topic_5y.items()}


def derive_window(by_topic_5y: dict[str, list], days: int) -> dict[str, list]:
    """The last `days` days of the 5-year series, rescaled to a shared 0–100 scale for all topics (as Google does)."""
    window = _window(by_topic_5y, days)
    peak = max((v for pts in window.values() for _, v in pts), default=0)
    return {t: [[d, round(v * 100 / peak) if peak else 0] for d, v in pts] for t, pts in window.items()}


def derive_12m(by_topic_5y: dict[str, list]) -> dict[str, list]:
    return derive_window(by_topic_5y, DERIVE_DAYS["12m"])


def low_resolution(by_topic_5y: dict[str, list]) -> bool:
    """The last 52 weeks are far below the 5-year peak: a derived 12-month series would have only a few levels (0, 1, 2…)."""
    peak = max((v for pts in _window(by_topic_5y, DERIVE_DAYS["12m"]).values() for _, v in pts), default=0)
    return 0 < peak < LOW_RES_PEAK


async def _fetch(
    conn: sqlite3.Connection, topics: list[str], names: list[str], geo: str, source: str, hl: str, backup: bool = True
) -> dict:
    """Google first; when Google is blocking (now or just now) use DataForSEO if enabled (and backup=True).

    {"series": {range: {topic: [[date, value]]}}, "errors": {range: error}, "provider": "google" | "dataforseo"}.
    """
    timeframes = {n: TIMEFRAMES[n] for n in names}
    if (errors := _cooldown_errors(conn, names)) is None:
        # pytrends is synchronous, so it runs in a thread; the cache is written on the main thread (sqlite is thread-bound).
        got = await asyncio.to_thread(_fetch_many, topics, timeframes, geo, source, hl)
        if not got["throttled"]:
            return {"series": got["series"], "errors": got["errors"], "provider": "google"}
        _start_cooldown(conn)
        errors = got["errors"]
    if not backup or dfs.unavailable_reason(conn, "dig"):
        return {"series": {}, "errors": errors, "provider": "google"}
    series: dict[str, dict] = {}
    for name in names:
        try:
            dfs._guard(conn, "dig")
            loc = dfs.location_code(conn, geo)
            try:
                series[name], cost = await asyncio.to_thread(dfs.fetch_series, loc, topics, source, name)
            except dfs.BackupError:
                dfs.record(conn, 0)
                raise
            dfs.record(conn, cost)
        except dfs.BackupError as e:
            failed = {n: f"{THROTTLED}; {BACKUP_FAILED}: {e}" for n in names if n not in series}
            return {"series": series, "errors": failed, "provider": "dataforseo"}
    return {"series": series, "errors": {}, "provider": "dataforseo"}


# ---------------------------------------------------------------------------
# One topic (a dig)
# ---------------------------------------------------------------------------


def _tf_key(topic: str, geo: str, source: str, name: str) -> str:
    return cache.make_key("trends_tf", {"topic": topic, "geo": geo, "source": source, "timeframe": name})


async def get_trends(
    conn: sqlite3.Connection, topic: str, geo: str, source: str = "youtube", hl: str = "vi-VN", fresh: bool = False
) -> dict:
    """5-year series (1 call); 12 months and 3 months derived from its tail. Cached ranges are reused.

    {"series": {"5y": [...], ...}, "errors": {range: error}, "derived": ranges derived from 5 years, "provider", "cached"}.
    """
    series: dict[str, list] = {}
    if not fresh:
        for name in TIMEFRAMES:
            if (hit := cache.get(conn, _tf_key(topic, geo, source, name))) is not None:
                series[name] = hit
    errors: dict[str, str] = {}
    providers: list[str] = []

    async def need(names: list[str], backup: bool = True) -> None:
        got = await _fetch(conn, [topic], names, geo, source, hl, backup)
        for name, by_topic in got["series"].items():
            series[name] = by_topic[topic]
            cache.put(conn, _tf_key(topic, geo, source, name), series[name], TTL_TRENDS)
        errors.update(got["errors"])
        providers.append(got["provider"])

    if "5y" not in series:
        await need(["5y"])
    # Derived 12-month scale too coarse (this year far below the 5-year peak): fetch separately, but only while Google allows it.
    if "12m" not in series and series.get("5y") and not errors and "dataforseo" not in providers:
        if low_resolution({topic: series["5y"]}):
            await need(["12m"], backup=False)
    derived = []
    for name, days in DERIVE_DAYS.items():
        if name not in series and series.get("5y"):
            series[name] = derive_window({topic: series["5y"]}, days)[topic]
            derived.append(name)
    if errors:
        errors = {n: errors.get(n, SKIPPED) for n in TIMEFRAMES if n not in series}
    return {
        "series": {n: series[n] for n in TIMEFRAMES if n in series},
        "errors": errors,
        "derived": derived,
        "provider": "dataforseo" if "dataforseo" in providers else "google",
        "topic": topic,
        "geo": geo,
        "source": source,
        "cached": not providers,
    }


# ---------------------------------------------------------------------------
# Several topics on one scale (lesson 2.2: the main niche with up to 4 sub-niches)
# ---------------------------------------------------------------------------

COMPARE_TIMEFRAMES = {"5y": "today 5-y", "12m": "today 12-m"}
MAX_COMPARE = 5  # Google Trends limit


async def compare_trends(
    conn: sqlite3.Connection, topics: list[str], geo: str, source: str = "youtube", hl: str = "vi-VN", fresh: bool = False
) -> dict:
    """{"series": {"5y": {topic: [[date, value]]}, "12m": {...}}, "errors": {...}}. One shared 0–100 scale.

    Only 5 years is fetched; 12 months is derived from the last 52 weeks (fetched separately when the scale is too coarse
    and Google allows it).
    """
    topics = list(dict.fromkeys(t.strip() for t in topics if t.strip()))[:MAX_COMPARE]
    key = cache.make_key("trends_compare", {"topics": topics, "geo": geo, "source": source})
    if not fresh and (hit := cache.get(conn, key)) is not None:
        return {**hit, "cached": True}
    got = await _fetch(conn, topics, ["5y"], geo, source, hl)
    series, errors = got["series"], got["errors"]
    if series.get("5y") and not errors and got["provider"] == "google" and low_resolution(series["5y"]):
        series.update((await _fetch(conn, topics, ["12m"], geo, source, hl, backup=False))["series"])
    derived = []
    if "12m" not in series and series.get("5y"):
        series["12m"] = derive_12m(series["5y"])
        derived.append("12m")
    if errors:
        errors = {n: errors.get(n, SKIPPED) for n in COMPARE_TIMEFRAMES if n not in series}
    result = {"series": series, "errors": errors, "derived": derived, "provider": got["provider"],
              "topics": topics, "geo": geo, "source": source}
    if not result["errors"]:
        cache.put(conn, key, result, TTL_TRENDS)
    return {**result, "cached": False}


# ---------------------------------------------------------------------------
# Keyword radar: official Google Trends data by category
# ---------------------------------------------------------------------------


class Throttled(RuntimeError):
    """Google returned 429: stop the scan tick and rest."""


def _call(run, hl: str = "en-US"):
    """run(client). On 429 retry with a new session + cookie (RETRY_WAITS); still 429 → Throttled."""
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            out = run(_client(hl))
            stats["rescued"] += bool(attempt)
            return out
        except Exception as e:
            if not _is_429(e):
                raise
            if attempt == len(RETRY_WAITS):
                raise Throttled(THROTTLED) from e
            time.sleep(RETRY_WAITS[attempt])


def category_tree(conn: sqlite3.Connection) -> dict:
    """Google Trends' official category tree (English names), cached 30 days."""
    key = "trends_categories:en"
    if (hit := cache.get(conn, key)) is not None:
        return hit
    tree = _call(lambda client: client.categories())
    cache.put(conn, key, tree, 30 * 86400)
    return tree


def category_queries(geo: str, category_id: int, timeframe: str = "today 3-m") -> dict[str, list[tuple[str, int]]]:
    """Top and rising queries of a whole category (no keyword needed), Web Search source.

    Category filtering on Trends' YouTube source is unreliable (Science returns "karaoke"), so Web is used.
    """
    def run(client):
        client.build_payload([""], cat=category_id, timeframe=timeframe, geo=geo, gprop="")
        return list(client.related_queries().values())[0]

    got = _call(run)
    out = {}
    for kind in ("top", "rising"):
        df = got.get(kind)
        out[kind] = [] if df is None else [(str(q), int(v)) for q, v in df[["query", "value"]].itertuples(index=False)]
    return out


def entity_types(query: str) -> list[dict]:
    """Google's entity suggestions for a query: [{"title", "type"}] (types in English: Singer, TV program...)."""
    # pytrends puts the query in the URL path: "/" (e.g. "bayern – bodø/glimt") makes Google return 404.
    clean = " ".join(query.replace("/", " ").replace("?", " ").replace("#", " ").split())
    got = _call(lambda client: client.suggestions(clean))
    return [{"title": s.get("title", ""), "type": s.get("type", "")} for s in got[:5]]


def series_30d(query: str, geo: str, source: str = "web") -> list:
    """Last 30 days (daily) of one query, Google Search (web) or YouTube Search source."""

    def run(client):
        client.build_payload([query], timeframe=TIMEFRAME_30D, geo=geo, gprop=SOURCES[source])
        df = client.interest_over_time()
        return [] if df.empty else [[idx.strftime("%Y-%m-%d"), int(v)] for idx, v in df[query].items()]

    return _call(run)


def start_cooldown(conn: sqlite3.Connection) -> None:
    _start_cooldown(conn)
