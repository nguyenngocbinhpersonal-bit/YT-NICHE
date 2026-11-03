"""Tách bộ mẫu Dài / Shorts và gắn nhãn định dạng phù hợp (bài 2.2)."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.stats import median

FIT_LABEL = {LONG: "Ngách hợp video dài", SHORTS: "Ngách hợp Shorts"}


def _summary(videos: list[dict]) -> dict:
    return {
        "n": len(videos),
        "median_views": median(v["views"] for v in videos),
        "outliers": sum(1 for v in videos if v["level"]),
    }


def format_split(videos: list[dict]) -> dict:
    """Một định dạng "hợp" khi thắng cả trung vị (≥ 1,5×) lẫn số video vượt trội."""
    long_s = _summary([v for v in videos if v["fmt"] == LONG])
    short_s = _summary([v for v in videos if v["fmt"] == SHORTS])

    fit = None
    if long_s["n"] >= cfg.FORMAT_MIN_N and short_s["n"] >= cfg.FORMAT_MIN_N:
        lm, sm = long_s["median_views"] or 0, short_s["median_views"] or 0
        if lm >= cfg.FORMAT_FIT_RATIO * sm and long_s["outliers"] >= short_s["outliers"]:
            fit = LONG
        elif sm >= cfg.FORMAT_FIT_RATIO * lm and short_s["outliers"] >= long_s["outliers"]:
            fit = SHORTS

    return {LONG: long_s, SHORTS: short_s, "fit": fit, "fit_label": FIT_LABEL.get(fit)}
