"""Danh sách thị trường duy nhất của app: bảng `market` trong DB, sửa ở Cài đặt → Thị trường.

Khu vực và ngôn ngữ lấy theo danh sách chính thức của Google (YouTube i18nRegions / i18nLanguages). apply() nạp bảng
vào config để mọi tính năng dùng chung:
- config.MARKETS: {mã: (relevanceLanguage, tên)} gồm cả thị trường đã tắt (tra tên / ngôn ngữ cho dữ liệu cũ);
- config.ENABLED_MARKETS: thị trường đang bật, theo thứ tự nhóm (dùng cho mọi ô chọn thị trường);
- config.MARKET_GROUPS: {nhóm: [mã đang bật]}.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from yt_niche import config as cfg
from yt_niche.sources.youtube import YouTubeClient

OTHER_GROUP = "Khác"
CATALOG_KEY = "google_markets"

# Ngôn ngữ chính của từng khu vực YouTube hỗ trợ (gợi ý khi bật khu vực mới). Mã theo relevanceLanguage.
REGION_LANGUAGE = {
    "AE": "ar", "AM": "hy", "AR": "es", "AT": "de", "AU": "en", "AZ": "az", "BA": "bs", "BD": "bn", "BE": "nl",
    "BG": "bg", "BH": "ar", "BO": "es", "BR": "pt", "BY": "ru", "CA": "en", "CH": "de", "CL": "es", "CO": "es",
    "CR": "es", "CY": "el", "CZ": "cs", "DE": "de", "DK": "da", "DO": "es", "DZ": "ar", "EC": "es", "EE": "et",
    "EG": "ar", "ES": "es", "FI": "fi", "FR": "fr", "GB": "en", "GE": "ka", "GH": "en", "GR": "el", "GT": "es",
    "HK": "zh-Hant", "HN": "es", "HR": "hr", "HU": "hu", "ID": "id", "IE": "en", "IL": "he", "IN": "hi", "IQ": "ar",
    "IS": "is", "IT": "it", "JM": "en", "JO": "ar", "JP": "ja", "KE": "en", "KH": "km", "KR": "ko", "KW": "ar",
    "KZ": "ru", "LA": "lo", "LB": "ar", "LI": "de", "LK": "si", "LT": "lt", "LU": "fr", "LV": "lv", "LY": "ar",
    "MA": "ar", "MD": "ro", "ME": "sr", "MK": "mk", "MT": "en", "MX": "es", "MY": "ms", "NG": "en", "NI": "es",
    "NL": "nl", "NO": "no", "NP": "ne", "NZ": "en", "OM": "ar", "PA": "es", "PE": "es", "PG": "en", "PH": "en",
    "PK": "ur", "PL": "pl", "PR": "es", "PT": "pt", "PY": "es", "QA": "ar", "RO": "ro", "RS": "sr", "RU": "ru",
    "SA": "ar", "SE": "sv", "SG": "en", "SI": "sl", "SK": "sk", "SN": "fr", "SV": "es", "TH": "th", "TN": "ar",
    "TR": "tr", "TW": "zh-Hant", "TZ": "sw", "UA": "uk", "UG": "en", "US": "en", "UY": "es", "VE": "es", "VN": "vi",
    "YE": "ar", "ZA": "en", "ZW": "en",
}
# Mã ngôn ngữ của Google (hl) ↔ relevanceLanguage của YouTube search.
_HL_TO_RELEVANCE = {"iw": "he", "fil": "tl", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "zh-HK": "zh-Hant", "nb": "no"}
_RELEVANCE_TO_HL = {"he": "iw", "tl": "fil", "zh-Hans": "zh-CN", "zh-Hant": "zh-TW"}


class MarketError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def relevance_language(hl: str) -> str:
    """Mã ngôn ngữ Google → relevanceLanguage: 'es-419' → 'es', 'zh-TW' → 'zh-Hant', 'iw' → 'he'."""
    if hl in _HL_TO_RELEVANCE:
        return _HL_TO_RELEVANCE[hl]
    if hl.startswith("zh"):
        return "zh-Hant" if "Hant" in hl else "zh-Hans"
    return hl.split("-")[0]


def default_hl(region: str) -> str:
    lang = REGION_LANGUAGE.get(region, "en")
    return _RELEVANCE_TO_HL.get(lang, lang)


# ---------------------------------------------------------------------------
# Danh sách chính thức của Google (lưu lại để dùng khi không có mạng / chưa có API key)
# ---------------------------------------------------------------------------


async def _fetch_catalog(conn: sqlite3.Connection) -> dict:
    async with YouTubeClient(conn) as yt:
        regions, languages = await asyncio.gather(yt.i18n("regions"), yt.i18n("languages"))
    return {"regions": regions, "languages": languages}


def refresh_catalog(conn: sqlite3.Connection) -> dict:
    """Tải lại 111 khu vực và 83 ngôn ngữ của Google (2 unit quota) và lưu vào DB."""
    got = asyncio.run(_fetch_catalog(conn))
    got["fetched_at"] = _now()
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (CATALOG_KEY, json.dumps(got, ensure_ascii=False)))
    return got


def catalog(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (CATALOG_KEY,)).fetchone()
    return json.loads(row["value"]) if row else None


# ---------------------------------------------------------------------------
# Bảng market
# ---------------------------------------------------------------------------


def _seed(conn: sqlite3.Connection) -> None:
    """Lần đầu: 17 thị trường mặc định + thị trường đã thêm ở bản cũ (bảng custom_market)."""
    if conn.execute("SELECT 1 FROM market LIMIT 1").fetchone():
        return
    group_of = {code: g for g, codes in cfg.DEFAULT_MARKET_GROUPS.items() for code in codes}
    rows = [
        (code, name, _RELEVANCE_TO_HL.get(lang, lang), group_of.get(code, OTHER_GROUP))
        for code, (lang, name) in cfg.DEFAULT_MARKETS.items()
    ]
    rows += [
        (m["region"], m["name"], _RELEVANCE_TO_HL.get(m["language"], m["language"]), OTHER_GROUP)
        for m in conn.execute("SELECT * FROM custom_market")
        if m["region"] not in cfg.DEFAULT_MARKETS
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO market (region, name, hl, group_name, enabled, updated_at) VALUES (?, ?, ?, ?, 1, ?)",
        [(*r, _now()) for r in rows],
    )


def list_markets(conn: sqlite3.Connection, enabled_only: bool = False) -> list[dict]:
    _seed(conn)
    q = "SELECT * FROM market" + (" WHERE enabled = 1" if enabled_only else "")
    rows = [dict(r) for r in conn.execute(q)]
    group_order = list(cfg.DEFAULT_MARKET_GROUPS)
    rows.sort(key=lambda r: (
        group_order.index(r["group_name"]) if r["group_name"] in group_order else len(group_order),
        r["group_name"], list(cfg.DEFAULT_MARKETS).index(r["region"]) if r["region"] in cfg.DEFAULT_MARKETS else 999,
        r["name"],
    ))
    return rows


def apply(conn: sqlite3.Connection) -> None:
    """Nạp bảng market vào config (sửa tại chỗ để mọi `from config import MARKETS` thấy cùng một dict)."""
    rows = list_markets(conn)
    cfg.MARKETS.clear()
    cfg.MARKETS.update({r["region"]: (relevance_language(r["hl"]), r["name"]) for r in rows})
    enabled = [r for r in rows if r["enabled"]]
    cfg.ENABLED_MARKETS[:] = [r["region"] for r in enabled]
    cfg.MARKET_GROUPS.clear()
    for r in enabled:
        cfg.MARKET_GROUPS.setdefault(r["group_name"] or OTHER_GROUP, []).append(r["region"])


def save(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """rows: [{region, name, hl, group_name, enabled}] cho các khu vực được sửa (khu vực khác giữ nguyên).

    Khu vực chưa có trong bảng chỉ được lưu khi bật; tắt thì giữ dòng (dữ liệu cũ vẫn tra được tên).
    """
    current = {r["region"]: r for r in list_markets(conn)}
    final = {code: bool(r["enabled"]) for code, r in current.items()}
    final.update({r["region"]: bool(r["enabled"]) for r in rows})
    if not any(final.values()):
        raise MarketError("Phải bật ít nhất một thị trường")
    langs = {lang["code"] for lang in (catalog(conn) or {}).get("languages", [])}
    for r in rows:
        if not r["enabled"] and r["region"] not in current:
            continue
        if not str(r.get("name") or "").strip() or not str(r.get("hl") or "").strip():
            raise MarketError(f"{r['region']}: thiếu tên hoặc ngôn ngữ")
        if langs and r["hl"] not in langs:
            raise MarketError(f"{r['region']}: ngôn ngữ \"{r['hl']}\" không có trong danh sách của Google")
        conn.execute(
            """INSERT INTO market (region, name, hl, group_name, enabled, updated_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(region) DO UPDATE SET name = excluded.name, hl = excluded.hl,
                   group_name = excluded.group_name, enabled = excluded.enabled, updated_at = excluded.updated_at""",
            (r["region"], r["name"].strip(), r["hl"].strip(), (r.get("group_name") or "").strip() or OTHER_GROUP,
             int(bool(r["enabled"])), _now()),
        )
    apply(conn)


def enable_language(conn: sqlite3.Connection, hl: str) -> list[str]:
    """Bật mọi khu vực của Google có ngôn ngữ chính là hl. Trả về mã đã bật thêm."""
    cat = catalog(conn)
    if not cat:
        raise MarketError("Chưa tải danh sách của Google")
    names = {r["code"]: r["name"] for r in cat["regions"]}
    lang = relevance_language(hl)
    current = {r["region"]: r for r in list_markets(conn)}
    targets = [c for c in names if REGION_LANGUAGE.get(c) == lang and not (current.get(c) or {}).get("enabled")]
    save(conn, [
        {"region": c, "name": (current.get(c) or {}).get("name") or names[c], "hl": hl,
         "group_name": (current.get(c) or {}).get("group_name") or OTHER_GROUP, "enabled": True}
        for c in targets
    ])
    return targets


def reset_defaults(conn: sqlite3.Connection) -> None:
    """Về 17 thị trường mặc định (khu vực khác bị tắt, không xoá)."""
    conn.execute("UPDATE market SET enabled = 0, updated_at = ?", (_now(),))
    save(conn, [
        {"region": code, "name": name, "hl": _RELEVANCE_TO_HL.get(lang, lang),
         "group_name": next((g for g, cs in cfg.DEFAULT_MARKET_GROUPS.items() if code in cs), OTHER_GROUP), "enabled": True}
        for code, (lang, name) in cfg.DEFAULT_MARKETS.items()
    ])
