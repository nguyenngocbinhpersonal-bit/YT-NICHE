"""The 10-criteria scorecard to lock in a niche (lesson 2.5): final score, red flags, main / backup niche."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from yt_niche.core.keyword_score import long_keyword_score
from yt_niche.core.money import RPM_HIGH, RPM_MEDIUM
from yt_niche.core.trends_classify import DOWN, FAD, FALLING, SEASONAL, STABLE

TIE_GAP = 5  # less than 5 points apart counts as a tie
RED_FLAG_SCORE = 1
MIN_MAIN_SCORE = 2  # the main niche may not have any cell below 2
YOUNG_BOOM_MONTHS = 6  # "has a booming channel under 6 months old"
SENSITIVE_CATEGORIES = ("Finance", "Health", "Spirituality")  # substring match against RPM category names


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: int
    guide: str
    auto: bool  # the app can suggest a score from the dig numbers


CRITERIA: list[Criterion] = [
    Criterion("demand", "Demand", 15, "Median views; 5 = highest among the niches being compared", True),
    Criterion("trend", "Trend", 10, "Google Trends 1 year / 5 years; 5 = evergreen or rising", True),
    Criterion("small_channels", "Small booming channels", 15, "5 = many small booming channels, some under 6 months old", True),
    Criterion("competition", "Competition (reverse)", 10, "5 = few channels over 1 million subs in the top results", True),
    Criterion("ad_price", "Ad price", 10, "By market and topic (RPM table)", True),
    Criterion("money_other", "Money beyond ads", 10, "Affiliates, own products, buying power; 5 = many products that fit naturally", False),
    Criterion("production", "Sustainable production", 10, "Can make 30+ videos, effort per video is manageable, plenty of material", False),
    Criterion("policy_risk", "Policy risk (reverse)", 10, "Lose points if prone to copyright claims, sensitive, medical / financial", False),
    Criterion("passion", "Your interest", 5, "Would you read / watch this topic when nobody pays you?", False),
    Criterion("long_keywords", "Long-tail keywords", 5, "5 = 10 or more long-tail keywords", False),
]
CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}
DEFAULT_WEIGHTS = {c.key: c.weight for c in CRITERIA}


# ---------------------------------------------------------------------------
# Final score and selection rules
# ---------------------------------------------------------------------------


def weights_error(weights: dict[str, int]) -> str | None:
    if set(weights) != set(DEFAULT_WEIGHTS):
        return "Criteria missing from the weights table"
    if any(w < 0 for w in weights.values()):
        return "Weights cannot be negative"
    total = sum(weights.values())
    return None if total == 100 else f"Weights must add up to 100 (currently {total})"


def final_score(weights: dict[str, int], scores: dict[str, int | None]) -> float | None:
    """Σ(weight × score 1–5) ÷ 5. None while a cell is unscored (cells with weight 0 are skipped)."""
    if any(scores.get(k) is None for k, w in weights.items() if w > 0):
        return None
    return round(sum(w * scores[k] for k, w in weights.items() if w > 0) / 5, 1)


def red_flags(weights: dict[str, int], scores: dict[str, int | None]) -> list[str]:
    return [k for k, w in weights.items() if w > 0 and scores.get(k) == RED_FLAG_SCORE]


def is_eligible(weights: dict[str, int], scores: dict[str, int | None]) -> bool:
    return all(scores.get(k) is not None and scores[k] >= MIN_MAIN_SCORE for k, w in weights.items() if w > 0)


def _tie_value(item: dict) -> int:
    return (item["scores"].get("production") or 0) + (item["scores"].get("money_other") or 0)


def _pick(candidates: list[dict]) -> tuple[dict | None, str]:
    """Highest score; when another niche is less than 5 points behind, decide by Sustainable production + Money beyond ads."""
    if not candidates:
        return None, ""
    ranked = sorted(candidates, key=lambda x: x["final"], reverse=True)
    top = ranked[0]
    close = [c for c in ranked if top["final"] - c["final"] < TIE_GAP]
    if len(close) == 1:
        return top, f"highest final score ({top['final']:g})"
    winner = max(close, key=lambda c: (_tie_value(c), c["final"]))
    names = ", ".join(f"{c['name']} {c['final']:g}" for c in close)
    return winner, (
        f"tied (gap < {TIE_GAP}: {names}), decided by Sustainable production + Money beyond ads "
        f"= {_tie_value(winner)}"
    )


def decide(items: list[dict], weights: dict[str, int]) -> dict:
    """items: [{"id", "name", "scores": {criterion: 1–5 | None}}].

    Returns the ranking, the main niche, the backup niche and the reasons. main/backup are None until fully scored.
    """
    rows = []
    for it in items:
        final = final_score(weights, it["scores"])
        rows.append(
            {
                **it,
                "final": final,
                "red_flags": red_flags(weights, it["scores"]),
                "eligible": final is not None and is_eligible(weights, it["scores"]),
            }
        )
    ranking = sorted(rows, key=lambda r: (r["final"] is None, -(r["final"] or 0)))
    complete = all(r["final"] is not None for r in rows)
    result = {"ranking": ranking, "complete": complete, "main": None, "backup": None, "main_reason": "", "backup_reason": ""}
    if not complete:
        return result

    eligible = [r for r in rows if r["eligible"]]
    main, why = _pick(eligible)
    if main:
        result["main"], result["main_reason"] = main, why
        backup, why_b = _pick([r for r in eligible if r["id"] != main["id"]])
        result["backup"], result["backup_reason"] = backup, why_b
    return result


# ---------------------------------------------------------------------------
# Automatic score suggestions from dig results
# ---------------------------------------------------------------------------

MANUAL = "score by hand"


def _score_demand(median: float | None, best: float | None) -> tuple[int | None, str]:
    if not median or not best:
        return None, "no median views"
    # Highest = 5; every halving loses 1 point.
    score = max(1, min(5, 5 - round(math.log2(best / median))))
    return score, f"median {median:,.0f} views (highest on the board: {best:,.0f})"


def _score_trend(trends: dict) -> tuple[int, str]:
    cls = trends.get("class")
    if cls == STABLE:
        score = 4 if trends.get("direction") == DOWN else 5
    elif cls == SEASONAL:
        score = 4
    elif cls in (FAD, FALLING):
        score = 2
    else:
        return 3, "no Trends yet"
    return score, f"Trends: {cls}" + (f", {trends['direction']}" if trends.get("direction") else "")


def _score_small_channels(cards: list[dict]) -> tuple[int, str]:
    n = len(cards)
    score = 5 if n >= 10 else 4 if n >= 5 else 3 if n >= 3 else 2 if n >= 1 else 1
    young = sum(1 for c in cards if c.get("age_months") is not None and c["age_months"] < YOUNG_BOOM_MONTHS)
    if young:
        score = min(5, score + 1)
    return score, f"{n} small booming channels, {young} under {YOUNG_BOOM_MONTHS} months old"


def _score_competition(competition: float) -> tuple[int, str]:
    c = competition
    score = 5 if c <= 10 else 4 if c <= 25 else 3 if c <= 40 else 2 if c <= 60 else 1
    return score, f"{c:.0f}% of top videos from channels > 1 million subs"


def _score_ad_price(rpm: list | tuple | None, tier: str | None) -> tuple[int | None, str]:
    if not rpm:
        return None, "no topic category picked, so no RPM"
    score = 5 if tier == RPM_HIGH else 3 if tier == RPM_MEDIUM else 2
    return score, f"RPM ≈ {rpm[0]:g}–{rpm[1]:g} USD ({tier} tier)"


def _score_money_other(affiliates: list[str]) -> tuple[int | None, str]:
    n = len(affiliates)
    if not n:
        return None, "no affiliate products listed"
    score = 5 if n >= 5 else 4 if n >= 3 else 3
    return score, f"{n} affiliate products: {', '.join(affiliates[:5])}"


def auto_suggestions(
    runs: list[dict], keywords: dict[int, list[dict]] | None = None
) -> dict[int, dict[str, tuple[int | None, str]]]:
    """runs: dig runs (dicts from db.runs, with "result", "affiliates", "category").
    keywords: {run_id: scored long-tail keywords of that niche} (lesson 2.3), when available.

    Returns {run_id: {criterion: (suggested score or None, rationale)}}. Manual cells carry a reminder as rationale.
    """
    keywords = keywords or {}
    best_median = max((r["result"]["median_views"] or 0 for r in runs), default=0) or None
    out = {}
    for run in runs:
        r = run["result"]
        category = run.get("category") or ""
        sensitive = any(s in category for s in SENSITIVE_CATEGORIES)
        policy_hint = f"category \"{category}\" is sensitive: consider a low score" if sensitive else MANUAL
        out[run["id"]] = {
            "demand": _score_demand(r["median_views"], best_median),
            "trend": _score_trend(r["trends"]),
            "small_channels": _score_small_channels(r["small_boom_channels"]),
            "competition": _score_competition(r["competition"]),
            "ad_price": _score_ad_price(r.get("rpm"), r.get("rpm_tier")),
            "money_other": _score_money_other(run.get("affiliates") or []),
            "production": (None, MANUAL),
            "policy_risk": (None, policy_hint),
            "passion": (None, MANUAL),
            "long_keywords": long_keyword_score(keywords.get(run["id"]) or []),
        }
    return out


def setup_warnings(runs: list[dict], max_days_apart: int = 3) -> list[str]:
    """Lesson 2.4: only compare niches dug with the same market, time window, format and on nearly the same day."""
    warnings = []
    for field, label in (("region", "market"), ("window_days", "time window"), ("fmt", "format")):
        values = {r[field] for r in runs}
        if len(values) > 1:
            warnings.append(f"Niches differ in {label}: {', '.join(map(str, sorted(values)))}")
    dates = sorted(r["created_at"][:10] for r in runs)
    if dates:
        span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
        if span > max_days_apart:
            warnings.append(f"Digs are {span} days apart (should be ≤ {max_days_apart} days); re-dig on the same day")
    return warnings
