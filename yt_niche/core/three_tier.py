"""Chấm 3 tầng Cầu / Cung / Tiền → THỬ / THỬ SAU KHI SỬA / BỎ (bài 2.1)."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.money import RPM_HIGH, RPM_MEDIUM
from yt_niche.core.trends_classify import FALLING, SEASONAL, STABLE

STRONG, MEDIUM, WEAK = "Mạnh", "Vừa", "Yếu"
TRY, TRY_AFTER_FIX, DROP = "THỬ", "THỬ SAU KHI SỬA", "BỎ"

_LEVEL = {WEAK: 1, MEDIUM: 2, STRONG: 3}
_BY_LEVEL = {1: WEAK, 2: MEDIUM, 3: STRONG}

# Bảng "thiếu tầng" của bài 2.1.
FIXES = {
    "demand": "Thiếu Cầu: nới rộng lên một tầng, hoặc đổi sang chủ đề người ta thật sự tìm.",
    "supply": "Thiếu Cung: đi xuống ngách con 2–3 tầng hoặc đổi đối tượng.",
    "money": "Thiếu Tiền: đổi thị trường hoặc thêm affiliate đúng nhu cầu.",
}


def rate_demand(demand_bar: float, trend_class: str | None) -> tuple[str, str]:
    reason = f"Nhu cầu {demand_bar:.0f}/100, Trends: {trend_class or 'chưa có'}"
    if demand_bar < cfg.TIER_DEMAND_WEAK or trend_class == FALLING:
        return WEAK, reason
    # Bài 2.1: đi ngang hoặc đi lên là cầu bền; ngách theo mùa vẫn có cầu lặp lại hằng năm.
    if demand_bar >= cfg.TIER_DEMAND_STRONG and trend_class in (STABLE, SEASONAL):
        return STRONG, reason
    return MEDIUM, reason


def rate_supply(outliers: int, small_channels: int, competition: float) -> tuple[str, str]:
    reason = f"{outliers} video vượt trội, {small_channels} kênh nhỏ nổ, Cạnh tranh {competition:.0f}%"
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
        parts.append(f"{affiliates} sản phẩm affiliate")
    if not levels:
        return MEDIUM, "chưa có RPM và affiliate, cần đo thêm"
    if affiliates is None:
        parts.append("chưa nhập affiliate")
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
