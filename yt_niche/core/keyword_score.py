"""Score a long-tail keyword 0–100 from real YouTube results (lesson 2.3): Demand and Competition.

The formula is the app's own assumption (the course does not publish one):
- Demand = median views of the result videos, log scale (KEYWORD_DEMAND_LOG_RANGE).
- Competition = weighted % of results from big channels (≥ KEYWORD_BIG_CHANNEL_SUBS) and % of titles that target the keyword exactly.
- Score = 50% Demand + 50% (100 − Competition).
"""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.stats import log_scale, median
from yt_niche.core.text import contains_all

VERY_GOOD, GOOD, AVERAGE, WEAK = "Very good", "Good", "Average (reserve)", "Weak"
LOW_DATA = "low data"


def label(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= cfg.KEYWORD_VERY_GOOD:
        return VERY_GOOD
    if score >= cfg.KEYWORD_GOOD:
        return GOOD
    if score >= cfg.KEYWORD_AVERAGE:
        return AVERAGE
    return WEAK


def score_keyword(keyword: str, results: list[dict], language: str) -> dict:
    """results: search result videos [{"title", "views", "subs", "channel_id"}] (subs None when the channel hides them)."""
    with_views = [v for v in results if v.get("views") is not None]
    n = len(with_views)
    med = median(v["views"] for v in with_views)
    demand = log_scale(med, *cfg.KEYWORD_DEMAND_LOG_RANGE)

    known_subs = [v for v in results if v.get("subs") is not None]
    big_share = (
        sum(v["subs"] >= cfg.KEYWORD_BIG_CHANNEL_SUBS for v in known_subs) / len(known_subs) * 100 if known_subs else 0.0
    )
    exact_share = sum(contains_all(v.get("title", ""), keyword, language) for v in results) / len(results) * 100 if results else 0.0
    w = cfg.KEYWORD_COMPETITION_WEIGHTS
    competition = (w["big_channels"] * big_share + w["exact_title"] * exact_share) / (w["big_channels"] + w["exact_title"])

    score = round(0.5 * demand + 0.5 * (100 - competition), 1)
    low_data = n < cfg.KEYWORD_LOW_DATA_N
    return {
        "keyword": keyword,
        "n": n,
        "median_views": med,
        "demand": round(demand, 1),
        "big_share": round(big_share, 1),
        "exact_share": round(exact_share, 1),
        "competition": round(competition, 1),
        "score": score,
        "label": label(score),
        "low_data": low_data,
    }


def long_keyword_score(scored: list[dict]) -> tuple[int | None, str]:
    """Suggestion for the scorecard's "Long-tail keywords" cell (lesson 2.5): 5 = at least 10 long-tail keywords scoring ≥ 55 without the low-data flag."""
    if not scored:
        return None, "no long-tail keywords scored yet (Sub-niches & keywords page)"
    good = [k for k in scored if k["score"] is not None and k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]]
    n = len(good)
    score = 5 if n >= 10 else 4 if n >= 7 else 3 if n >= 4 else 2 if n >= 1 else 1
    return score, f"{n} long-tail keywords at ≥ {cfg.KEYWORD_GOOD} ({len(scored)} scored)"
