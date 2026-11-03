"""Thu nhập ước tính mỗi video và bậc RPM (tầng Tiền, bài 2.1)."""

from __future__ import annotations

from yt_niche import config as cfg

RPM_LOW, RPM_MEDIUM, RPM_HIGH = "thấp", "vừa", "cao"


def income_per_video(median_views: float | None, rpm_low: float, rpm_high: float) -> tuple[float, float] | None:
    """≈ trung vị view × RPM ÷ 1000, dạng khoảng. Chỉ dùng để so ngách, không phải thu nhập thật."""
    if not median_views:
        return None
    return round(median_views * rpm_low / 1000, 1), round(median_views * rpm_high / 1000, 1)


def rpm_tier(rpm_low: float, rpm_high: float) -> str:
    mid = (rpm_low + rpm_high) / 2
    if mid >= cfg.RPM_TIER_HIGH_MIN:
        return RPM_HIGH
    if mid >= cfg.RPM_TIER_MEDIUM_MIN:
        return RPM_MEDIUM
    return RPM_LOW
