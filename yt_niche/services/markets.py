"""Đào đa thị trường (bài 2.4): một chủ đề, mỗi nước một từ khoá đã dịch, cùng thiết lập, cùng ngày."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

from yt_niche import config as cfg
from yt_niche.core.market import opportunity, winning_topics
from yt_niche.db import runs as run_repo
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError

# Unit ước tính cho một nước khi chưa cache kênh (1 trang search ≈ 200, 2 trang ≈ 320).
UNITS_PER_MARKET = {1: 200, 2: 320}
MAX_MARKETS = 10  # bài 2.4: chạy 6–10 nước cho ngách dẫn đầu


class MarketError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_units(n_markets: int, pages: int) -> int:
    return n_markets * UNITS_PER_MARKET[pages]


def create_batch(
    conn: sqlite3.Connection,
    topic: str,
    keywords: dict[str, str],
    *,
    category: str | None,
    window_days: int,
    fmt: str,
) -> int:
    """keywords: {mã nước: từ khoá đã dịch}. Bỏ nước để trống."""
    keywords = {r: " ".join(k.split()) for r, k in keywords.items() if r in cfg.ENABLED_MARKETS and k and k.strip()}
    if not topic.strip():
        raise MarketError("Nhập chủ đề gốc trước đã")
    if len(keywords) < 2:
        raise MarketError("Cần ít nhất 2 thị trường có từ khoá")
    if len(keywords) > MAX_MARKETS:
        raise MarketError(f"Tối đa {MAX_MARKETS} thị trường mỗi lần (mỗi nước tốn khoảng 200–320 unit)")
    cur = conn.execute(
        "INSERT INTO market_batch (topic, category, window_days, fmt, created_at) VALUES (?, ?, ?, ?, ?)",
        (topic.strip(), category, window_days, fmt, _now()),
    )
    bid = cur.lastrowid
    conn.executemany(
        "INSERT INTO market_batch_item (batch_id, region, keyword) VALUES (?, ?, ?)",
        [(bid, r, k) for r, k in keywords.items()],
    )
    return bid


def run_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    pages: int = 1,
    with_trends: bool = False,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Đào lần lượt các nước chưa có kết quả. Hết quota thì dừng, giữ các nước đã xong."""
    batch = conn.execute("SELECT * FROM market_batch WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise MarketError("Không tìm thấy lần đào đa thị trường")
    todo = conn.execute(
        "SELECT region, keyword FROM market_batch_item WHERE batch_id = ? AND run_id IS NULL ORDER BY rowid", (batch_id,)
    ).fetchall()
    done, stopped = 0, None
    for i, item in enumerate(todo):
        region = item["region"]
        if on_progress:
            on_progress(i, len(todo), region)
        params = DigParams(
            topic=item["keyword"],
            region=region,
            language=cfg.MARKETS[region][0],
            window_days=batch["window_days"],
            fmt=batch["fmt"],
            pages=pages,
            with_trends=with_trends,
        )
        try:
            run_id = dig(conn, params, category=batch["category"])
            conn.execute(
                "UPDATE market_batch_item SET run_id = ?, error = '' WHERE batch_id = ? AND region = ?",
                (run_id, batch_id, region),
            )
            done += 1
        except QuotaExceeded:
            stopped = "Hết quota YouTube hôm nay; các nước còn lại sẽ đào tiếp khi bấm lại sau khi quota reset."
            conn.execute(
                "UPDATE market_batch_item SET error = 'hết quota' WHERE batch_id = ? AND region = ?", (batch_id, region)
            )
            break
        except YouTubeError as e:
            conn.execute(
                "UPDATE market_batch_item SET error = ? WHERE batch_id = ? AND region = ?", (str(e)[:200], batch_id, region)
            )
    return {"done": done, "total": len(todo), "stopped": stopped}


def list_batches(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT b.*, COUNT(i.region) AS n, SUM(i.run_id IS NOT NULL) AS n_done
           FROM market_batch b LEFT JOIN market_batch_item i ON i.batch_id = b.id
           GROUP BY b.id ORDER BY b.created_at DESC, b.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def load_batch(conn: sqlite3.Connection, batch_id: int) -> dict | None:
    batch = conn.execute("SELECT * FROM market_batch WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        return None
    items = []
    for it in conn.execute("SELECT * FROM market_batch_item WHERE batch_id = ? ORDER BY rowid", (batch_id,)).fetchall():
        row = {"region": it["region"], "keyword": it["keyword"], "run_id": it["run_id"], "error": it["error"], "run": None}
        run = run_repo.get(conn, it["run_id"], with_raw=True) if it["run_id"] else None
        if run:
            r = run["result"]
            row["run"] = {k: run[k] for k in ("id", "topic", "region", "created_at", "confidence", "category")}
            row["result"] = r
            row["opportunity"] = opportunity(r["score"], r.get("rpm"), (run["raw"].get("trends") or {}).get("series"))
            row["topics"] = winning_topics(r["outliers"], it["keyword"])
            booms = r["small_boom_channels"]
            row["sample_channel"] = min(booms, key=lambda c: (c["age_months"] is None, c["age_months"] or 0)) if booms else None
        items.append(row)
    def rank(x: dict) -> tuple:
        score = (x.get("opportunity") or {}).get("score")
        return (score is None, -(score or 0), -((x.get("result") or {}).get("score") or 0))

    items.sort(key=rank)
    return {**dict(batch), "items": items}


def delete_batch(conn: sqlite3.Connection, batch_id: int) -> None:
    """Xoá lần đào đa thị trường; các lần đào từng nước vẫn giữ ở Ngách đã lưu."""
    conn.execute("DELETE FROM market_batch WHERE id = ?", (batch_id,))
