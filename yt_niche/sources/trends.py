"""Google Trends qua pytrends-modern (bản pytrends còn được bảo trì, chế độ HTTP), có cache SQLite.

Google trả 429 khi yêu cầu không có cookie NID còn tốt (đo thật 10/2026: không cookie 3/3 lần 429, cookie mới 3/3 qua,
một phiên giữ cookie gọi liền 7 lần đều qua), và khi gọi quá nhiều theo IP. Vì vậy:
- cùng một User-Agent trình duyệt cho lúc lấy cookie lẫn lúc gọi dữ liệu;
- 429 thì bỏ cookie, tạo phiên mới (cookie mới) rồi thử lại;
- cache riêng từng khoảng thời gian, lần sau chỉ lấy khoảng còn thiếu;
- thử lại 2 lần như vậy vẫn 429 thì dừng hẳn và nghỉ COOLDOWN_SECONDS, trong thời gian nghỉ không gọi Google;
- chỉ gọi chuỗi 5 năm; 12 tháng và 3 tháng suy từ đoạn cuối của nó (chỉ gọi riêng 12 tháng khi thang quá thô);
- Google đang chặn mà đã bật DataForSEO (Cài đặt → Google Trends) thì lấy qua DataForSEO (trả phí theo lượt).
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

TIMEFRAMES = {"5y": "today 5-y", "12m": "today 12-m", "3m": "today 3-m"}  # 5 năm trước: quan trọng nhất
TIMEFRAME_LABELS = {"5y": "5 năm", "12m": "12 tháng", "3m": "3 tháng"}
SOURCES = {"web": "", "youtube": "youtube"}
TIMEFRAME_30D = "today 1-m"  # Radar: 30 ngày gần nhất, theo ngày
EXPLORE_URL = "https://trends.google.com/trends/explore"
PAUSE_SECONDS = 1.5
RETRY_WAITS = (5, 20)  # giây chờ trước mỗi lần thử lại (bằng phiên + cookie mới) khi bị 429
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
# Số lần 429 được cứu nhờ cookie mới (đọc rồi đặt lại bởi Radar để ghi vào nhịp quét).
stats = {"rescued": 0}
COOLDOWN_SECONDS = 600
COOLDOWN_KEY = "trends_cooldown:ip"
THROTTLED = "Google tạm chặn vì gọi quá nhiều (429)"
SKIPPED = "bỏ qua vì Google đang tạm chặn"
BACKUP_FAILED = "DataForSEO lỗi"
DERIVE_DAYS = {"12m": 365, "3m": 91}  # khoảng suy từ chuỗi 5 năm (theo tuần)
LOW_RES_PEAK = 25  # đỉnh 52 tuần cuối trong chuỗi 5 năm dưới mức này: thang quá thô, gọi riêng 12 tháng


def _is_429(e: Exception) -> bool:
    return "429" in str(e) or type(e).__name__ == "TooManyRequestsError"


def explore_url(keyword: str, geo: str, source: str = "web", timeframe: str = TIMEFRAMES["5y"]) -> str:
    """Link trang Google Trends (mở trong trình duyệt) cho một từ khoá, thị trường và nguồn."""
    params = {"q": keyword, "geo": geo, "date": timeframe}
    if SOURCES[source]:
        params["gprop"] = SOURCES[source]
    return f"{EXPLORE_URL}?{urlencode(params)}"


def _client(hl: str) -> TrendReq:
    """Phiên mới: tải trang chủ Trends lấy cookie NID mới. Chế độ HTTP (không mở trình duyệt, không đăng nhập Google).

    Một User-Agent cố định cho cả lấy cookie lẫn gọi dữ liệu (đổi UA giữa chừng trông như bot).
    Tắt retries/backoff của thư viện: thư viện thử lại với cookie cũ, vô ích; app tự thử lại bằng phiên mới.
    """
    return TrendReq(hl=hl, tz=-420, timeout=(10, 30), retries=0, backoff_factor=0, rotate_user_agent=False,
                    requests_args={"headers": {"User-Agent": USER_AGENT}})


def _fetch_many(topics: list[str], timeframes: dict[str, str], geo: str, source: str, hl: str) -> dict:
    """Gọi pytrends (đồng bộ). {"series": {khoảng: {chủ đề: [[ngày, giá trị]]}}, "errors": {...}, "throttled": bool}."""
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
                        client = _client(hl)  # cookie cũ có thể đã bị đánh dấu: lấy cookie mới
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
# Thời gian nghỉ sau khi bị chặn
# ---------------------------------------------------------------------------


def cooldown_until(conn: sqlite3.Connection) -> datetime | None:
    """Giờ hết nghỉ (giờ máy), hoặc None nếu đang được gọi Google."""
    until = cache.get(conn, COOLDOWN_KEY)
    return datetime.fromtimestamp(until) if until and until > time.time() else None


def _start_cooldown(conn: sqlite3.Connection) -> None:
    cache.put(conn, COOLDOWN_KEY, time.time() + COOLDOWN_SECONDS, COOLDOWN_SECONDS)


def _cooldown_errors(conn: sqlite3.Connection, names) -> dict[str, str] | None:
    if until := cooldown_until(conn):
        return {n: f"{THROTTLED}, app tạm nghỉ gọi Trends đến {until:%H:%M}" for n in names}
    return None


def describe_errors(errors: dict[str, str] | None) -> str | None:
    """Câu báo lỗi dễ đọc cho giao diện."""
    if not errors:
        return None
    missing = ", ".join(TIMEFRAME_LABELS.get(n, n) for n in errors)
    backup = next((v.split(f"{BACKUP_FAILED}: ", 1)[1] for v in errors.values() if f"{BACKUP_FAILED}: " in v), None)
    if backup:
        return f"Thiếu Trends {missing}: Google tạm chặn máy bạn, nguồn dự phòng DataForSEO cũng không lấy được ({backup})."
    if any(THROTTLED in v or SKIPPED in v for v in errors.values()):
        tail = next((v.split(", ", 1)[1] for v in errors.values() if ", app tạm nghỉ" in v), None)
        wait = f" ({tail})" if tail else f" (app tạm nghỉ gọi Trends {COOLDOWN_SECONDS // 60} phút)"
        return (
            f"Thiếu Trends {missing}: Google tạm chặn máy bạn vì gọi quá nhiều yêu cầu{wait}. "
            "Lỗi này thường tự hết sau 10–60 phút; khi đó bấm \"Lấy lại Google Trends\" (không tốn quota YouTube)."
        )
    return f"Thiếu Trends {missing}: " + "; ".join(f"{TIMEFRAME_LABELS.get(n, n)}: {v}" for n, v in errors.items())


# ---------------------------------------------------------------------------
# Suy 12 tháng từ 5 năm
# ---------------------------------------------------------------------------


def _window(by_topic_5y: dict[str, list], days: int) -> dict[str, list]:
    last = max((date.fromisoformat(pts[-1][0]) for pts in by_topic_5y.values() if pts), default=None)
    if last is None:
        return {t: [] for t in by_topic_5y}
    start = (last - timedelta(days=days)).isoformat()
    return {t: [[d, v] for d, v in pts if d > start] for t, pts in by_topic_5y.items()}


def derive_window(by_topic_5y: dict[str, list], days: int) -> dict[str, list]:
    """Đoạn `days` ngày cuối của chuỗi 5 năm, đưa về thang 0–100 chung cho mọi chủ đề (như Google làm)."""
    window = _window(by_topic_5y, days)
    peak = max((v for pts in window.values() for _, v in pts), default=0)
    return {t: [[d, round(v * 100 / peak) if peak else 0] for d, v in pts] for t, pts in window.items()}


def derive_12m(by_topic_5y: dict[str, list]) -> dict[str, list]:
    return derive_window(by_topic_5y, DERIVE_DAYS["12m"])


def low_resolution(by_topic_5y: dict[str, list]) -> bool:
    """52 tuần cuối quá thấp so với đỉnh 5 năm: suy ra 12 tháng sẽ chỉ còn vài mức (0, 1, 2…)."""
    peak = max((v for pts in _window(by_topic_5y, DERIVE_DAYS["12m"]).values() for _, v in pts), default=0)
    return 0 < peak < LOW_RES_PEAK


async def _fetch(
    conn: sqlite3.Connection, topics: list[str], names: list[str], geo: str, source: str, hl: str, backup: bool = True
) -> dict:
    """Google trước; Google đang / vừa chặn thì DataForSEO nếu đã bật (và backup=True).

    {"series": {khoảng: {chủ đề: [[ngày, giá trị]]}}, "errors": {khoảng: lỗi}, "provider": "google" | "dataforseo"}.
    """
    timeframes = {n: TIMEFRAMES[n] for n in names}
    if (errors := _cooldown_errors(conn, names)) is None:
        # pytrends đồng bộ nên chạy ở thread riêng; ghi cache ở thread chính (sqlite gắn với thread).
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
# Một chủ đề (lần đào)
# ---------------------------------------------------------------------------


def _tf_key(topic: str, geo: str, source: str, name: str) -> str:
    return cache.make_key("trends_tf", {"topic": topic, "geo": geo, "source": source, "timeframe": name})


async def get_trends(
    conn: sqlite3.Connection, topic: str, geo: str, source: str = "youtube", hl: str = "vi-VN", fresh: bool = False
) -> dict:
    """Chuỗi 5 năm (1 lần gọi); 12 tháng và 3 tháng suy từ đoạn cuối của nó. Khoảng nào có trong cache thì dùng lại.

    {"series": {"5y": [...], ...}, "errors": {khoảng: lỗi}, "derived": khoảng suy từ 5 năm, "provider", "cached"}.
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
    # Thang 12 tháng suy ra quá thô (năm nay thấp hơn nhiều so với đỉnh 5 năm): gọi riêng, nhưng chỉ khi Google đang cho gọi.
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
# So nhiều chủ đề trên cùng một thang (bài 2.2: ngách chính với tối đa 4 ngách con)
# ---------------------------------------------------------------------------

COMPARE_TIMEFRAMES = {"5y": "today 5-y", "12m": "today 12-m"}
MAX_COMPARE = 5  # giới hạn của Google Trends


async def compare_trends(
    conn: sqlite3.Connection, topics: list[str], geo: str, source: str = "youtube", hl: str = "vi-VN", fresh: bool = False
) -> dict:
    """{"series": {"5y": {chủ đề: [[ngày, giá trị]]}, "12m": {...}}, "errors": {...}}. Cùng một thang 0–100.

    Chỉ gọi 5 năm; 12 tháng suy từ 52 tuần cuối (gọi riêng khi thang quá thô và Google đang cho gọi).
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
# Radar từ khoá: dữ liệu chính thức của Google Trends theo danh mục
# ---------------------------------------------------------------------------


class Throttled(RuntimeError):
    """Google trả 429: dừng lượt quét và nghỉ."""


def _call(run, hl: str = "en-US"):
    """run(client). 429 thì thử lại bằng phiên + cookie mới (RETRY_WAITS); vẫn 429 thì Throttled."""
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
    """Cây danh mục chính thức của Google Trends (tên tiếng Anh), cache 30 ngày."""
    key = "trends_categories:en"
    if (hit := cache.get(conn, key)) is not None:
        return hit
    tree = _call(lambda client: client.categories())
    cache.put(conn, key, tree, 30 * 86400)
    return tree


def category_queries(geo: str, category_id: int, timeframe: str = "today 3-m") -> dict[str, list[tuple[str, int]]]:
    """Truy vấn hàng đầu và đang tăng của cả một danh mục (không cần từ khoá), nguồn Web Search.

    Lọc danh mục trên nguồn YouTube của Trends không đáng tin (Khoa học ra "karaoke"), nên dùng Web.
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
    """Gợi ý thực thể của Google cho truy vấn: [{"title", "type"}] (loại bằng tiếng Anh: Singer, TV program...)."""
    # pytrends đặt truy vấn vào đường dẫn URL: "/" (vd. "bayern – bodø/glimt") làm Google trả 404.
    clean = " ".join(query.replace("/", " ").replace("?", " ").replace("#", " ").split())
    got = _call(lambda client: client.suggestions(clean))
    return [{"title": s.get("title", ""), "type": s.get("type", "")} for s in got[:5]]


def series_30d(query: str, geo: str, source: str = "web") -> list:
    """Chuỗi 30 ngày gần nhất (theo ngày) của một truy vấn, nguồn Google Search (web) hoặc YouTube Search."""

    def run(client):
        client.build_payload([query], timeframe=TIMEFRAME_30D, geo=geo, gprop=SOURCES[source])
        df = client.interest_over_time()
        return [] if df.empty else [[idx.strftime("%Y-%m-%d"), int(v)] for idx, v in df[query].items()]

    return _call(run)


def start_cooldown(conn: sqlite3.Connection) -> None:
    _start_cooldown(conn)
