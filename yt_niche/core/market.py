"""Multi-market opportunity score (lesson 2.4).

- With Google Trends for that country: 45% niche score + 30% search demand + 25% earning power.
- Without Trends: 60% niche score + 40% ad price.
- 65 and above is the green zone.

How "search demand" and "ad price" are mapped to 0–100 is the app's own assumption:
- Search demand = average interest over the last 12 months relative to the high (90th percentile) of the 5 years.
  Trends only gives relative numbers; the 90th percentile replaces the peak so seasonal topics are not punished for one peak week.
- Ad price = average RPM on a log scale (OPPORTUNITY_RPM_LOG_RANGE).
"""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.stats import clamp, log_scale
from yt_niche.core.text import tokens

WEEKS_PER_YEAR = 52


def search_level(series: dict | None) -> float | None:
    points = (series or {}).get("5y") or []
    values = [float(v) for _, v in points]
    if len(values) < WEEKS_PER_YEAR or max(values, default=0) <= 0:
        return None
    recent = values[-WEEKS_PER_YEAR:]
    if sum(v == 0 for v in recent) > len(recent) * cfg.TRENDS_SPARSE_ZERO_SHARE:
        return None  # sparse data: treat as no Trends
    high = sorted(values)[int(len(values) * 0.9)]
    return round(clamp(sum(recent) / len(recent) / (high or max(values)) * 100), 1)


def money_level(rpm: list | tuple | None) -> float | None:
    if not rpm:
        return None
    return round(log_scale((rpm[0] + rpm[1]) / 2, *cfg.OPPORTUNITY_RPM_LOG_RANGE), 1)


def opportunity(niche_score: float, rpm: list | tuple | None, trends_series: dict | None) -> dict:
    money = money_level(rpm)
    search = search_level(trends_series)
    if money is None:
        return {"score": None, "formula": "no RPM (pick a topic category)", "money": None, "search": search}
    if search is not None:
        w = cfg.OPPORTUNITY_WEIGHTS
        score = w["niche"] * niche_score + w["search"] * search + w["money"] * money
        formula = f"{w['niche']:.0%} niche score + {w['search']:.0%} search + {w['money']:.0%} money"
    else:
        w = cfg.OPPORTUNITY_WEIGHTS_NO_TRENDS
        score = w["niche"] * niche_score + w["money"] * money
        formula = f"{w['niche']:.0%} niche score + {w['money']:.0%} ad price (no Trends)"
    score = round(score, 1)
    return {"score": score, "green": score >= cfg.OPPORTUNITY_GREEN_MIN, "formula": formula, "money": money, "search": search}


def winning_topics(outliers: list[dict], keyword: str, top: int = 5) -> list[str]:
    """Winning sub-topics in one country: the phrases repeated most in outperformer titles (keyword words removed)."""
    from yt_niche.core.subniche import repeated_phrases

    phrases = repeated_phrases(outliers, keyword, min_channels=2, top=top)
    if phrases:
        return [p["phrase"] for p in phrases]
    # Too few videos: return a few leading titles as examples.
    kw = set(tokens(keyword))
    return [v["title"] for v in outliers[:3] if set(tokens(v["title"])) - kw]
