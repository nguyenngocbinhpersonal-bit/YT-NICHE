"""Một lần đào trọn vẹn: lấy dữ liệu → tính điểm → lưu. Và tính lại khi đổi affiliate / RPM / ngưỡng."""

from __future__ import annotations

import asyncio
import sqlite3

from yt_niche.core.analyze import analyze
from yt_niche.db import runs as run_repo
from yt_niche.services.fetch import DigParams, fetch_niche
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.trends import TIMEFRAMES, get_trends

# Các chỉ số hiện khi so số cũ / mới.
COMPARE_FIELDS = [
    ("score", "Điểm ngách"),
    ("demand", "Nhu cầu"),
    ("opportunity", "Cơ hội"),
    ("freshness", "Độ mới"),
    ("competition", "Cạnh tranh"),
    ("median_views", "Trung vị view"),
    ("n", "Số video mẫu"),
]


def _analyze(raw: dict, category: str | None, affiliates: list[str]) -> dict:
    rpm = rpm_source.lookup(raw["params"]["region"], category)
    # Chưa nhập sản phẩm nào → coi như chưa chấm affiliate (không phải 0 sản phẩm).
    return analyze(raw, rpm=rpm, affiliates=len(affiliates) if affiliates else None)


def dig(
    conn: sqlite3.Connection,
    params: DigParams,
    *,
    category: str | None,
    affiliates: list[str] | None = None,
    fresh: bool = False,
    parent_id: int | None = None,
    filter_type: str = "",
) -> int:
    """parent_id: ngách mẹ khi đào ngách con (bài 2.3); tầng = tầng ngách mẹ + 1."""
    raw = asyncio.run(fetch_niche(conn, params, fresh=fresh))
    affiliates = affiliates or []
    layer = 1
    if parent_id is not None and (parent := run_repo.get(conn, parent_id)):
        layer = parent["layer"] + 1
    return run_repo.insert(
        conn,
        raw=raw,
        result=_analyze(raw, category, affiliates),
        category=category,
        affiliates=affiliates,
        parent_id=parent_id,
        layer=layer,
        filter_type=filter_type,
    )


def redig(conn: sqlite3.Connection, run_id: int) -> int:
    """Đào lại cùng thiết lập với số mới; giữ nhóm chủ đề và affiliate của lần trước."""
    old = run_repo.get(conn, run_id, with_raw=True)
    params = DigParams(**old["raw"]["params"])
    return dig(
        conn, params, category=old["category"], affiliates=old["affiliates"], fresh=True,
        parent_id=old["parent_id"], filter_type=old["filter_type"],
    )


def reanalyze(conn: sqlite3.Connection, run_id: int, *, category: str | None, affiliates: list[str]) -> dict:
    """Tính lại từ dữ liệu thô đã lưu (không tốn quota)."""
    run = run_repo.get(conn, run_id, with_raw=True)
    result = _analyze(run["raw"], category, affiliates)
    run_repo.update_result(conn, run_id, result, category=category, affiliates=affiliates)
    return result


def refetch_trends(conn: sqlite3.Connection, run_id: int) -> dict:
    """Lấy lại riêng Google Trends cho lần đào đã lưu (không tốn quota YouTube) rồi tính lại điểm.

    Khoảng thời gian nào đã có trong cache thì dùng lại, chỉ gọi Google cho khoảng còn thiếu.
    """
    run = run_repo.get(conn, run_id, with_raw=True)
    p = run["raw"]["params"]
    trends = asyncio.run(get_trends(conn, p["topic"], p["region"], p.get("trends_source") or "youtube"))
    # Giữ khoảng đã có của lần trước nếu lần này vẫn chưa lấy được.
    old = run["raw"].get("trends") or {}
    series = {**(old.get("series") or {}), **trends["series"]}
    merged = {
        **trends,
        "series": {n: series[n] for n in TIMEFRAMES if n in series},
        "errors": {n: e for n, e in trends["errors"].items() if n not in series},
    }
    run_repo.update_raw(conn, run_id, {**run["raw"], "trends": merged})
    reanalyze(conn, run_id, category=run["category"], affiliates=run["affiliates"])
    return merged


def compare(old: dict, new: dict) -> list[dict]:
    rows = []
    for key, label in COMPARE_FIELDS:
        a, b = old["result"].get(key), new["result"].get(key)
        rows.append({"Chỉ số": label, "Cũ": a, "Mới": b, "Chênh": (b - a) if a is not None and b is not None else None})
    for key, label in [("total", "Video vượt trội")]:
        a, b = old["result"]["outlier_counts"][key], new["result"]["outlier_counts"][key]
        rows.append({"Chỉ số": label, "Cũ": a, "Mới": b, "Chênh": b - a})
    a, b = len(old["result"]["small_boom_channels"]), len(new["result"]["small_boom_channels"])
    rows.append({"Chỉ số": "Kênh nhỏ nổ", "Cũ": a, "Mới": b, "Chênh": b - a})
    for key, label in [("label", "Nhãn"), ("confidence", "Độ tin cậy"), ("verdict", "Kết luận")]:
        rows.append({"Chỉ số": label, "Cũ": old[key], "Mới": new[key], "Chênh": None})
    rows.append(
        {"Chỉ số": "Trends", "Cũ": old["result"]["trends"]["class"], "Mới": new["result"]["trends"]["class"], "Chênh": None}
    )
    return rows
