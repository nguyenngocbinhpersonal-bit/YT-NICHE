"""DataForSEO: nguồn Google Trends dự phòng (trả phí theo lượt) khi Google chặn máy bạn (429).

Chỉ dùng khi Google đang chặn và đã bật ở Cài đặt → Google Trends; có giới hạn số lượt mỗi ngày.
Chỉ gửi từ khoá, mã nước, nguồn (Web / YouTube), khoảng thời gian, mã danh mục. Đăng nhập bằng login / password API
của DataForSEO (Basic auth), lưu trong .env như các key khác.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime

import httpx

from yt_niche import config as cfg
from yt_niche.db import cache

BASE_URL = "https://api.dataforseo.com/v3"
CONFIG_KEY = "dataforseo_config"
DEFAULT_CONFIG = {"enabled": False, "radar": True, "max_tasks_per_day": 40}
FEATURES = {
    "dig": "Đào ngách, so sánh ngách con, lấy lại Trends (bạn tự bấm)",
    "radar": "Radar từ khoá chạy nền (tốn lượt đều đặn khi Google chặn lâu)",
}
PRICE_PER_TASK = 0.009  # USD, chế độ Live (số thật lấy từ trường "cost" của mỗi lần gọi)
TIME_RANGES = {"5y": "past_5_years", "12m": "past_12_months", "3m": "past_90_days", "1m": "past_30_days"}
LOCATIONS_KEY = "dataforseo_locations"
LOCATIONS_TTL = 30 * 86400
TIMEOUT = 90
# DataForSEO không nhận các ký tự này trong từ khoá.
_BAD_CHARS = re.compile(r"[<>|\"\-+=~!:*()\[\]{}]")


class BackupError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cấu hình, lượt dùng
# ---------------------------------------------------------------------------


def credentials() -> tuple[str, str]:
    return os.getenv("DATAFORSEO_LOGIN", "").strip(), os.getenv("DATAFORSEO_PASSWORD", "").strip()


def save_credentials(login: str, password: str) -> None:
    from dotenv import set_key

    cfg.ENV_PATH.touch(exist_ok=True)
    for name, value in (("DATAFORSEO_LOGIN", login.strip()), ("DATAFORSEO_PASSWORD", password.strip())):
        set_key(str(cfg.ENV_PATH), name, value, quote_mode="never")
        os.environ[name] = value


def masked_login() -> str:
    login = credentials()[0]
    return f"{login[:3]}…{login[-4:]}" if len(login) > 8 else ("****" if login else "")


def get_config(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (CONFIG_KEY,)).fetchone()
    return {**DEFAULT_CONFIG, **(json.loads(row["value"]) if row else {})}


def save_config(conn: sqlite3.Connection, config: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (CONFIG_KEY, json.dumps({**get_config(conn), **config}))
    )


def _usage_key() -> str:
    return f"dataforseo_usage:{datetime.now().astimezone().date().isoformat()}"


def usage_today(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (_usage_key(),)).fetchone()
    return json.loads(row["value"]) if row else {"tasks": 0, "cost": 0.0}


def record(conn: sqlite3.Connection, cost: float) -> None:
    """Ghi một lượt gọi (kể cả lượt lỗi, để lỗi lặp lại không gọi mãi) và chi phí thật."""
    u = usage_today(conn)
    u = {"tasks": u["tasks"] + 1, "cost": round(u["cost"] + float(cost or 0), 5)}
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (_usage_key(), json.dumps(u)))


def unavailable_reason(conn: sqlite3.Connection, feature: str = "dig") -> str | None:
    c = get_config(conn)
    if not c["enabled"]:
        return "chưa bật DataForSEO"
    if not all(credentials()):
        return "chưa nhập login / password DataForSEO"
    if feature == "radar" and not c["radar"]:
        return "Radar không dùng DataForSEO"
    if usage_today(conn)["tasks"] >= int(c["max_tasks_per_day"]):
        return f"DataForSEO đã dùng hết {c['max_tasks_per_day']} lượt hôm nay"
    return None


def available(conn: sqlite3.Connection, feature: str = "dig") -> bool:
    return unavailable_reason(conn, feature) is None


# ---------------------------------------------------------------------------
# Gọi API
# ---------------------------------------------------------------------------


def _request(method: str, path: str, payload: list | None = None) -> dict:
    """Tách riêng để test thay thế. Lỗi HTTP / lỗi của DataForSEO → BackupError."""
    login, password = credentials()
    if not (login and password):
        raise BackupError("chưa nhập login / password DataForSEO")
    try:
        r = httpx.request(method, BASE_URL + path, json=payload, auth=(login, password), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise BackupError(f"không kết nối được DataForSEO: {type(e).__name__}") from e
    try:
        body = r.json()
    except ValueError:
        raise BackupError(f"HTTP {r.status_code}: {r.text[:150]}") from None
    if r.status_code >= 400 or body.get("status_code") != 20000:
        raise BackupError(f"{body.get('status_code') or r.status_code}: {str(body.get('status_message') or '')[:150]}")
    task = (body.get("tasks") or [{}])[0]
    if task.get("status_code") not in (None, 20000):
        raise BackupError(f"{task['status_code']}: {str(task.get('status_message') or '')[:150]}")
    return body


def _result(body: dict) -> list:
    return ((body.get("tasks") or [{}])[0].get("result")) or []


def account_balance() -> float:
    """Số dư tài khoản (USD). Không tính phí: dùng để kiểm tra login / password."""
    res = _result(_request("GET", "/appendix/user_data"))
    return float(((res[0] if res else {}).get("money") or {}).get("balance") or 0)


def location_code(conn: sqlite3.Connection, geo: str) -> int | None:
    """Mã nước của DataForSEO cho mã ISO (US → 2840). geo rỗng = toàn cầu (None)."""
    if not geo:
        return None
    codes = cache.get(conn, LOCATIONS_KEY)
    if codes is None:
        codes = {
            str(r.get("country_iso_code") or "").upper(): r["location_code"]
            for r in _result(_request("GET", "/keywords_data/google_trends/locations"))
            if r.get("location_type") == "Country" and r.get("country_iso_code")
        }
        cache.put(conn, LOCATIONS_KEY, codes, LOCATIONS_TTL)
    if geo.upper() not in codes:
        raise BackupError(f"DataForSEO không có dữ liệu Trends cho nước {geo}")
    return codes[geo.upper()]


def clean_keyword(kw: str) -> str:
    return " ".join(_BAD_CHARS.sub(" ", kw).split())[:100]


def _explore(task: dict, item_type: str) -> tuple[list, float]:
    body = _request("POST", "/keywords_data/google_trends/explore/live", [{**task, "item_types": [item_type]}])
    res = _result(body)
    items = [i for i in ((res[0] or {}).get("items") or []) if i.get("type") == item_type] if res else []
    return items, float(body.get("cost") or 0)


def _base_task(loc: int | None, source: str, name: str) -> dict:
    task = {"type": "youtube" if source == "youtube" else "web", "language_code": "en", "time_range": TIME_RANGES[name]}
    if loc is not None:
        task["location_code"] = loc
    return task


def fetch_series(loc: int | None, topics: list[str], source: str, name: str) -> tuple[dict[str, list], float]:
    """Chuỗi Trends của tối đa 5 chủ đề trên cùng một thang, như pytrends: ({chủ đề: [[ngày, giá trị]]}, chi phí)."""
    items, cost = _explore({**_base_task(loc, source, name), "keywords": [clean_keyword(t) for t in topics]}, "google_trends_graph")
    out: dict[str, list] = {t: [] for t in topics}
    for p in (items[0].get("data") or []) if items else []:
        values = p.get("values") or []
        for i, t in enumerate(topics):
            v = values[i] if i < len(values) else None
            out[t].append([p["date_from"], int(v or 0)])
    return out, cost


def fetch_category_queries(loc: int | None, category_id: int) -> tuple[dict[str, list[tuple[str, int]]], float]:
    """Truy vấn hàng đầu / đang tăng của cả một danh mục (Web Search, 3 tháng), như trends.category_queries."""
    items, cost = _explore({**_base_task(loc, "web", "3m"), "category_code": int(category_id)}, "google_trends_queries_list")
    data = (items[0].get("data") or {}) if items else {}
    return {k: [(str(q["query"]), int(q.get("value") or 0)) for q in (data.get(k) or [])] for k in ("top", "rising")}, cost


# ---------------------------------------------------------------------------
# Gọi đồng bộ kèm kiểm giới hạn (Radar)
# ---------------------------------------------------------------------------


def _guard(conn: sqlite3.Connection, feature: str) -> None:
    if reason := unavailable_reason(conn, feature):
        raise BackupError(reason)


def series(conn: sqlite3.Connection, feature: str, topics: list[str], geo: str, source: str, name: str) -> dict[str, list]:
    _guard(conn, feature)
    loc = location_code(conn, geo)
    cost = 0.0
    try:
        out, cost = fetch_series(loc, topics, source, name)
    finally:
        record(conn, cost)
    return out


def category_queries(conn: sqlite3.Connection, feature: str, geo: str, category_id: int) -> dict[str, list[tuple[str, int]]]:
    _guard(conn, feature)
    loc = location_code(conn, geo)
    cost = 0.0
    try:
        out, cost = fetch_category_queries(loc, category_id)
    finally:
        record(conn, cost)
    return out
