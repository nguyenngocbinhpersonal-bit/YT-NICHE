"""Nghiên cứu từ khoá (bài 2.3): lấy gợi ý → chấm điểm bằng kết quả YouTube thật → chọn → đào."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from yt_niche import config as cfg
from yt_niche.core.keyword_score import score_keyword
from yt_niche.services.fetch import _normalize_video, published_after
from yt_niche.sources.suggest import suggestions
from yt_niche.sources.youtube import YouTubeClient

# Mỗi từ khoá: 1 trang search (100) + videos (1) + channels (1–2).
UNITS_PER_KEYWORD = 102
MAX_SCORE_BATCH = 10  # bài 2.3: chấm 10 từ đầu, hoặc tick rồi chấm
MAX_DIG_PICKED = 5  # tick 3–5 từ tốt nhất rồi đào


class KeywordError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
    d["low_data"] = None if d["low_data"] is None else bool(d["low_data"])
    d["picked"] = bool(d["picked"])
    return d


def fetch_suggestions(conn: sqlite3.Connection, seed: str, region: str, *, az: bool = False) -> list[dict]:
    """Lấy gợi ý tìm kiếm YouTube (miễn phí) và lưu vào bảng keyword; không ghi đè điểm đã chấm."""
    seed = " ".join(seed.lower().split())
    if not seed:
        raise KeywordError("Nhập ngách con trước đã")
    language = cfg.MARKETS[region][0]
    items = asyncio.run(suggestions(conn, seed, region, language, az=az))
    for it in items:
        conn.execute(
            """INSERT INTO keyword (region, keyword, seed, language, source, rank, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(region, seed, keyword) DO NOTHING""",
            (region, it["keyword"], seed, language, it["source"], it["rank"], _now()),
        )
    _share_scores(conn, region)
    return items


def _share_scores(conn: sqlite3.Connection, region: str) -> None:
    """Từ khoá đã chấm dưới từ gốc khác thì mang điểm sang (không tốn quota chấm lại)."""
    conn.execute(
        """UPDATE keyword AS k SET scored_at = s.scored_at, score = s.score, demand = s.demand,
               competition = s.competition, n = s.n, low_data = s.low_data, metrics_json = s.metrics_json,
               run_id = COALESCE(k.run_id, s.run_id)
           FROM (SELECT * FROM keyword WHERE region = ? AND score IS NOT NULL) AS s
           WHERE k.region = s.region AND k.keyword = s.keyword AND k.score IS NULL""",
        (region,),
    )


def add_manual(conn: sqlite3.Connection, seed: str, region: str, keywords: list[str]) -> int:
    seed = " ".join(seed.lower().split())
    language = cfg.MARKETS[region][0]
    added = 0
    for kw in keywords:
        kw = " ".join(kw.lower().split())
        if kw:
            added += conn.execute(
                """INSERT INTO keyword (region, keyword, seed, language, source, created_at) VALUES (?, ?, ?, ?, 'manual', ?)
                   ON CONFLICT(region, seed, keyword) DO NOTHING""",
                (region, kw, seed, language, _now()),
            ).rowcount
    _share_scores(conn, region)
    return added


def list_keywords(conn: sqlite3.Connection, seed: str | None = None, region: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM keyword WHERE 1=1", []
    if seed is not None:
        sql += " AND seed = ?"
        args.append(" ".join(seed.lower().split()))
    if region is not None:
        sql += " AND region = ?"
        args.append(region)
    sql += " ORDER BY score IS NULL, score DESC, source = 'az', rank, keyword"
    return [_row(r) for r in conn.execute(sql, args).fetchall()]


def seeds(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT seed, region, COUNT(*) AS n, SUM(score IS NOT NULL) AS scored, MAX(created_at) AS last
           FROM keyword GROUP BY seed, region ORDER BY last DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def set_picked(conn: sqlite3.Connection, region: str, keywords: list[str], picked: bool) -> None:
    conn.executemany(
        "UPDATE keyword SET picked = ? WHERE region = ? AND keyword = ?", [(int(picked), region, k) for k in keywords]
    )


def delete(conn: sqlite3.Connection, region: str, keywords: list[str]) -> None:
    conn.executemany("DELETE FROM keyword WHERE region = ? AND keyword = ?", [(region, k) for k in keywords])


async def _score_many(conn: sqlite3.Connection, region: str, keywords: list[str], fresh: bool) -> tuple[list[dict], int]:
    language = cfg.MARKETS[region][0]
    after = published_after(cfg.KEYWORD_WINDOW_DAYS)
    async with YouTubeClient(conn, fresh=fresh) as yt:

        async def one(kw: str) -> dict:
            ids = await yt.search_videos(
                kw, region_code=region, language=language, published_after=after, order="relevance", pages=1
            )
            videos = [n for v in await yt.videos(ids) if (n := _normalize_video(v))]
            chans = await yt.channels(list(dict.fromkeys(v["channel_id"] for v in videos)))
            subs = {
                c["id"]: None if c.get("statistics", {}).get("hiddenSubscriberCount") else int(c["statistics"].get("subscriberCount", 0))
                for c in chans
            }
            results = [{**v, "subs": subs.get(v["channel_id"])} for v in videos]
            return score_keyword(kw, results, language)

        scored = await asyncio.gather(*(one(k) for k in keywords))
    return scored, yt.meter.total


def score(conn: sqlite3.Connection, region: str, keywords: list[str], *, fresh: bool = False) -> int:
    """Chấm điểm các từ khoá (tối đa 10 mỗi lần). Trả về số unit quota đã tiêu."""
    if not keywords:
        raise KeywordError("Chưa chọn từ khoá nào")
    if len(keywords) > MAX_SCORE_BATCH:
        raise KeywordError(f"Mỗi lần chấm tối đa {MAX_SCORE_BATCH} từ (≈ {MAX_SCORE_BATCH * UNITS_PER_KEYWORD} unit)")
    scored, units = asyncio.run(_score_many(conn, region, keywords, fresh))
    for s in scored:
        metrics = {k: s[k] for k in ("median_views", "big_share", "exact_share", "label")}
        conn.execute(
            """UPDATE keyword SET scored_at = ?, score = ?, demand = ?, competition = ?, n = ?, low_data = ?,
                   metrics_json = ? WHERE region = ? AND keyword = ?""",
            (_now(), s["score"], s["demand"], s["competition"], s["n"], int(s["low_data"]),
             json.dumps(metrics, ensure_ascii=False), region, s["keyword"]),
        )
    return units


def link_run(conn: sqlite3.Connection, region: str, keyword: str, run_id: int) -> None:
    conn.execute("UPDATE keyword SET run_id = ? WHERE region = ? AND keyword = ?", (run_id, region, keyword))


def scored_for(conn: sqlite3.Connection, topic: str, region: str) -> list[dict]:
    """Từ khoá đã chấm, lấy gợi ý từ chủ đề này (dùng gợi ý ô "Từ khoá dài" của bảng chấm)."""
    return [k for k in list_keywords(conn, topic, region) if k["score"] is not None]
