"""Rate the three tiers Demand / Supply / Money → TRY / TRY AFTER FIXING / DROP (lesson 2.1)."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.money import RPM_HIGH, RPM_MEDIUM
from yt_niche.core.trends_classify import FALLING, SEASONAL, STABLE

STRONG, MEDIUM, WEAK = "Strong", "Medium", "Weak"
TRY, TRY_AFTER_FIX, DROP = "TRY", "TRY AFTER FIXING", "DROP"

_LEVEL = {WEAK: 1, MEDIUM: 2, STRONG: 3}
_BY_LEVEL = {1: WEAK, 2: MEDIUM, 3: STRONG}

# The "missing tier" table of lesson 2.1.
FIXES = {
    "demand": "Weak Demand: widen one layer up, or switch to a topic people actually search for.",
    "supply": "Weak Supply: go down 2–3 sub-niche layers or change the audience.",
    "money": "Weak Money: change market or add affiliate products that match the need.",
}


def rate_demand(demand_bar: float, trend_class: str | None) -> tuple[str, str]:
    reason = f"Demand {demand_bar:.0f}/100, Trends: {trend_class or 'none'}"
    if demand_bar < cfg.TIER_DEMAND_WEAK or trend_class == FALLING:
        return WEAK, reason
    # Lesson 2.1: flat or rising is evergreen demand; a seasonal niche still has demand that returns every year.
    if demand_bar >= cfg.TIER_DEMAND_STRONG and trend_class in (STABLE, SEASONAL):
        return STRONG, reason
    return MEDIUM, reason


def rate_supply(outliers: int, small_channels: int, competition: float) -> tuple[str, str]:
    reason = f"{outliers} outperformer videos, {small_channels} small booming channels, Competition {competition:.0f}%"
    if small_channels < cfg.SUPPLY_WEAK_SMALL_CHANNELS or competition > cfg.TIER_COMPETITION_WEAK:
        return WEAK, reason
    if outliers >= cfg.SUPPLY_GOOD_OUTLIERS and small_channels >= cfg.SUPPLY_GOOD_SMALL_CHANNELS:
        return STRONG, reason
    return MEDIUM, reason


def rate_money(rpm_tier: str | None, affiliates: int | None) -> tuple[str, str]:
    parts, levels = [], []
    if rpm_tier is not None:
        levels.append(3 if rpm_tier == RPM_HIGH else 2 if rpm_tier == RPM_MEDIUM else 1)
        parts.append(f"RPM {rpm_tier}")
    if affiliates is not None:
        levels.append(3 if affiliates >= 3 else 2 if affiliates >= 1 else 1)
        parts.append(f"{affiliates} affiliate products")
    if not levels:
        return MEDIUM, "no RPM and no affiliates yet, measure more"
    if affiliates is None:
        parts.append("no affiliates entered")
    return _BY_LEVEL[sum(levels) // len(levels)], ", ".join(parts)


def three_tier(
    *,
    demand_bar: float,
    trend_class: str | None,
    outliers: int,
    small_channels: int,
    competition: float,
    rpm_tier: str | None,
    affiliates: int | None,
) -> dict:
    tiers = {
        "demand": rate_demand(demand_bar, trend_class),
        "supply": rate_supply(outliers, small_channels, competition),
        "money": rate_money(rpm_tier, affiliates),
    }
    weak = [k for k, (level, _) in tiers.items() if level == WEAK]
    verdict = TRY if not weak else TRY_AFTER_FIX if len(weak) == 1 else DROP
    return {
        **{k: {"level": level, "reason": reason} for k, (level, reason) in tiers.items()},
        "verdict": verdict,
        "fixes": [FIXES[k] for k in weak],
    }
