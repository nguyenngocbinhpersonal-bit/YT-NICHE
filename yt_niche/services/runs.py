"""One complete dig: fetch data → score → save. Plus re-scoring when affiliates / RPM / thresholds change."""

from __future__ import annotations

import asyncio
import sqlite3

from yt_niche.core.analyze import analyze
from yt_niche.db import runs as run_repo
from yt_niche.services.fetch import DigParams, fetch_niche
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.trends import TIMEFRAMES, get_trends

# Metrics shown when comparing old / new numbers.
COMPARE_FIELDS = [
    ("score", "Niche score"),
    ("demand", "Demand"),
    ("opportunity", "Opportunity"),
    ("freshness", "Freshness"),
    ("competition", "Competition"),
    ("median_views", "Median views"),
    ("n", "Sample videos"),
]


def _analyze(raw: dict, category: str | None, affiliates: list[str]) -> dict:
    rpm = rpm_source.lookup(raw["params"]["region"], category)
    # No products entered yet → treat affiliates as not scored (not as 0 products).
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
    """parent_id: the parent niche when digging a sub-niche (lesson 2.3); layer = parent's layer + 1."""
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
    """Dig again with the same settings and fresh numbers; keeps the previous topic category and affiliates."""
    old = run_repo.get(conn, run_id, with_raw=True)
    params = DigParams(**old["raw"]["params"])
    return dig(
        conn, params, category=old["category"], affiliates=old["affiliates"], fresh=True,
        parent_id=old["parent_id"], filter_type=old["filter_type"],
    )


def reanalyze(conn: sqlite3.Connection, run_id: int, *, category: str | None, affiliates: list[str]) -> dict:
    """Re-score from the stored raw data (no quota spent)."""
    run = run_repo.get(conn, run_id, with_raw=True)
    result = _analyze(run["raw"], category, affiliates)
    run_repo.update_result(conn, run_id, result, category=category, affiliates=affiliates)
    return result


def refetch_trends(conn: sqlite3.Connection, run_id: int) -> dict:
    """Fetch only Google Trends again for a saved dig (no YouTube quota) and re-score.

    Timeframes already in the cache are reused; Google is called only for the missing ones.
    """
    run = run_repo.get(conn, run_id, with_raw=True)
    p = run["raw"]["params"]
    trends = asyncio.run(get_trends(conn, p["topic"], p["region"], p.get("trends_source") or "youtube"))
    # Keep the timeframes from last time if this call still could not fetch them.
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
        rows.append({"Metric": label, "Old": a, "New": b, "Change": (b - a) if a is not None and b is not None else None})
    for key, label in [("total", "Outperformer videos")]:
        a, b = old["result"]["outlier_counts"][key], new["result"]["outlier_counts"][key]
        rows.append({"Metric": label, "Old": a, "New": b, "Change": b - a})
    a, b = len(old["result"]["small_boom_channels"]), len(new["result"]["small_boom_channels"])
    rows.append({"Metric": "Small booming channels", "Old": a, "New": b, "Change": b - a})
    for key, label in [("label", "Label"), ("confidence", "Confidence"), ("verdict", "Verdict")]:
        rows.append({"Metric": label, "Old": old[key], "New": new[key], "Change": None})
    rows.append(
        {"Metric": "Trends", "Old": old["result"]["trends"]["class"], "New": new["result"]["trends"]["class"], "Change": None}
    )
    return rows
