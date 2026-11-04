"""Classify Google Trends: evergreen / fad / declining / seasonal (lesson 2.2)."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from yt_niche import config as cfg

STABLE, FAD, FALLING, SEASONAL = "evergreen", "fad", "declining", "seasonal"
# Direction of the 5-year trend line (also used for the parent/sub-niche comparison in core.subniche).
UP, FLAT, DOWN, NO_DATA = "up", "flat", "down", "no data"

ADVICE = {
    STABLE: "Evergreen demand: you can build a whole channel on it.",
    FAD: "Fad: make a few quick videos, do not build a channel on it.",
    FALLING: "Declining: only enter if the score is still high; needs a fresh angle and a backup niche.",
    SEASONAL: "Seasonal: prepare videos 4–6 weeks before the peak.",
}

WEEKS_PER_YEAR = 52
RECENT_WEEKS = 13


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    avg, x_mean = mean(values), (n - 1) / 2
    return sum((i - x_mean) * (v - avg) for i, v in enumerate(values)) / sum((i - x_mean) ** 2 for i in range(n))


def relative_trend(values: list[float]) -> float:
    """Change of the linear regression line from the start to the end of the series, divided by the mean."""
    avg = mean(values) if values else 0
    if len(values) < 2 or avg == 0:
        return 0.0
    return _slope(values) * (len(values) - 1) / avg


def detrend(values: list[float]) -> list[float]:
    """Subtract the linear trend line so a rising/falling series is not mistaken for seasonal."""
    slope, avg, x_mean = _slope(values), mean(values), (len(values) - 1) / 2
    return [v - (avg + slope * (i - x_mean)) for i, v in enumerate(values)]


def autocorrelation(values: list[float], lag: int) -> float:
    n = len(values)
    if n <= lag + 1:
        return 0.0
    avg = mean(values)
    denom = sum((v - avg) ** 2 for v in values)
    if denom == 0:
        return 0.0
    return sum((values[i] - avg) * (values[i + lag] - avg) for i in range(n - lag)) / denom


def peak_month(points: list[list]) -> int:
    """Month (1–12) with the highest average value across the years."""
    by_month: dict[int, list[float]] = defaultdict(list)
    for date, value in points:
        by_month[int(date[5:7])].append(value)
    return max(by_month, key=lambda m: mean(by_month[m]))


def classify_trends(series: dict[str, list[list]] | None) -> dict:
    """series = {"5y": [[date, value], ...], "12m": [...], "3m": [...]} (weekly for 5y/12m)."""
    points_5y = (series or {}).get("5y") or []
    if len(points_5y) < WEEKS_PER_YEAR * 2:
        return {"class": None, "reason": "not enough 5-year Trends data yet", "sparse": True}

    values = [float(v) for _, v in points_5y]
    sparse = sum(v == 0 for v in values) / len(values) > cfg.TRENDS_SPARSE_ZERO_SHARE
    base = mean(values[: -WEEKS_PER_YEAR]) if len(values) > WEEKS_PER_YEAR else mean(values)
    recent = mean(values[-RECENT_WEEKS:])
    rel_5y = relative_trend(values)
    values_12m = [float(v) for _, v in (series.get("12m") or [])] or values[-WEEKS_PER_YEAR:]
    rel_12m = relative_trend(values_12m)
    ac = autocorrelation(detrend(values), WEEKS_PER_YEAR)

    metrics = {
        "base_mean": round(base, 1),
        "recent_mean": round(recent, 1),
        "trend_5y": round(rel_5y, 2),
        "trend_12m": round(rel_12m, 2),
        "autocorr_52w": round(ac, 2),
    }

    direction = UP if rel_5y > 0.1 else FLAT if rel_5y > cfg.TRENDS_FALLING_5Y else DOWN
    if base <= cfg.TRENDS_FAD_BASE_MAX and recent >= cfg.TRENDS_FAD_RECENT_MIN and recent >= cfg.TRENDS_FAD_RATIO * max(base, 1):
        cls, reason = FAD, f"4-year base ≈ {base:.1f}, last 13 weeks ≈ {recent:.1f}"
    elif ac >= cfg.TRENDS_SEASONAL_AUTOCORR:
        cls = SEASONAL
        reason = (
            f"repeats yearly (52-week autocorrelation = {ac:.2f}), peak in month {peak_month(points_5y)}, "
            f"5-year base {direction} ({rel_5y:+.0%})"
        )
    elif rel_5y <= cfg.TRENDS_FALLING_5Y and rel_12m < 0:
        cls, reason = FALLING, f"5 years {rel_5y:+.0%}, 12 months {rel_12m:+.0%}"
    else:
        cls, reason = STABLE, f"5 years {direction} ({rel_5y:+.0%}), 12 months {rel_12m:+.0%}"

    if sparse:
        reason += "; sparse data (many zero weeks), check the chart by eye"
    result = {"class": cls, "direction": direction, "reason": reason, "advice": ADVICE[cls], "sparse": sparse, **metrics}
    if cls == SEASONAL:
        result["peak_month"] = peak_month(points_5y)
    return result
