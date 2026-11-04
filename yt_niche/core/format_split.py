"""Split the sample into Long-form / Shorts and label the format that fits (lesson 2.2)."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.stats import median

FIT_LABEL = {LONG: "Fits long-form", SHORTS: "Fits Shorts"}


def _summary(videos: list[dict]) -> dict:
    return {
        "n": len(videos),
        "median_views": median(v["views"] for v in videos),
        "outliers": sum(1 for v in videos if v["level"]),
    }


def format_split(videos: list[dict]) -> dict:
    """A format "fits" when it wins both the median (≥ 1.5×) and the number of outperformer videos."""
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
