"""Thresholds editable on the Settings page: overrides saved in data/settings.json and applied to yt_niche.config at runtime."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from yt_niche import config as cfg


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    group: str
    kind: str  # int | float | pair | weights
    help: str = ""


EDITABLE: list[Setting] = [
    Setting("OUTLIER_OUTPERFORM", "Outperformer from (×)", "Outlier", "float"),
    Setting("OUTLIER_VIRAL", "Viral from (×)", "Outlier", "float"),
    Setting("OUTLIER_SUPER_VIRAL", "Super viral from (×)", "Outlier", "float"),
    Setting("MIN_CHANNEL_SAMPLE", "Minimum videos to trust a channel median", "Outlier", "int"),
    Setting("SMALL_CHANNEL_MAX_SUBS", "Small channel: under (subs)", "Channels", "int"),
    Setting("BIG_CHANNEL_MIN_SUBS", "Giant channel: over (subs)", "Channels", "int"),
    Setting("YOUNG_CHANNEL_MONTHS", "Young channel: under (months)", "Channels", "int"),
    Setting("NICHE_WEIGHTS", "Niche score weights", "Niche score", "weights", "Must add up to 1"),
    Setting("DEMAND_LOG_RANGE", "Demand: log10 median views (0 points, 100 points)", "Niche score", "pair", "3 = 1K views, 6 = 1M views"),
    Setting("FRESHNESS_VPH_LOG_RANGE", "Freshness: log10 views/hour (0 points, 100 points)", "Niche score", "pair"),
    Setting("LABEL_GREEN_MIN", "Green niche from", "Niche score", "float"),
    Setting("LABEL_POTENTIAL_MIN", "Promising niche from", "Niche score", "float"),
    Setting("CONFIDENCE_HIGH_MIN_N", "High confidence: minimum videos", "Confidence", "int"),
    Setting("CONFIDENCE_HIGH_MAX_ESTIMATED", "High confidence: maximum estimated share", "Confidence", "float"),
    Setting("CONFIDENCE_LOW_MAX_N", "Low confidence: under (videos)", "Confidence", "int"),
    Setting("CONFIDENCE_LOW_MIN_ESTIMATED", "Low confidence: estimated share over", "Confidence", "float"),
    Setting("SUPPLY_GOOD_OUTLIERS", "Good supply: outperformer videos", "Three tiers", "int"),
    Setting("SUPPLY_GOOD_SMALL_CHANNELS", "Good supply: small booming channels", "Three tiers", "int"),
    Setting("SUPPLY_WEAK_SMALL_CHANNELS", "Weak supply: under (small booming channels)", "Three tiers", "int"),
    Setting("TIER_DEMAND_STRONG", "Strong demand: Demand bar from", "Three tiers", "float"),
    Setting("TIER_DEMAND_WEAK", "Weak demand: Demand bar under", "Three tiers", "float"),
    Setting("TIER_COMPETITION_WEAK", "Weak supply: Competition over (%)", "Three tiers", "float"),
    Setting("RPM_TIER_MEDIUM_MIN", "Medium RPM from (USD)", "Three tiers", "float"),
    Setting("RPM_TIER_HIGH_MIN", "High RPM from (USD)", "Three tiers", "float"),
    Setting("FORMAT_FIT_RATIO", "Long/Shorts: wins when the median is (×)", "Long/Shorts", "float"),
    Setting("FORMAT_MIN_N", "Long/Shorts: minimum videos per side", "Long/Shorts", "int"),
    Setting("TRENDS_FAD_BASE_MAX", "Fad: 4-year base at most", "Trends", "float"),
    Setting("TRENDS_FAD_RECENT_MIN", "Fad: last 13 weeks at least", "Trends", "float"),
    Setting("TRENDS_FAD_RATIO", "Fad: at least (×) the base", "Trends", "float"),
    Setting("TRENDS_SEASONAL_AUTOCORR", "Seasonal: 52-week autocorrelation from", "Trends", "float"),
    Setting("TRENDS_FALLING_5Y", "Declining: 5-year trend from (negative)", "Trends", "float"),
    Setting("SUBNICHE_MIN_CHANNELS", "Repeated phrase: minimum different channels", "Keywords", "int"),
    Setting("KEYWORD_LONG_MIN_WORDS", "Long-tail keyword: from (words / syllables)", "Keywords", "int"),
    Setting("KEYWORD_LONG_MIN_CHARS", "Long-tail keyword (Japanese, Korean, Thai, Chinese): from (characters)", "Keywords", "int"),
    Setting("KEYWORD_WINDOW_DAYS", "Keyword scoring: videos within (days)", "Keywords", "int"),
    Setting("KEYWORD_DEMAND_LOG_RANGE", "Keywords: log10 median views (0 points, 100 points)", "Keywords", "pair", "2 = 100 views, 5 = 100K views"),
    Setting("KEYWORD_BIG_CHANNEL_SUBS", "Keywords: big competitor from (subs)", "Keywords", "int"),
    Setting("KEYWORD_COMPETITION_WEIGHTS", "Keywords: competition weights", "Keywords", "weights", "Big channels and exact-match titles; must add up to 1"),
    Setting("KEYWORD_LOW_DATA_N", "Keyword \"low data\": under (results)", "Keywords", "int"),
    Setting("COMPARE_SAMPLE_RATIO", "Compare: warn when sample sizes differ by (×)", "Compare", "float"),
    Setting("OPPORTUNITY_RPM_LOG_RANGE", "Opportunity score: log10 RPM (0 points, 100 points)", "Compare", "pair", "-0.7 ≈ 0.2 USD; 1 = 10 USD"),
    Setting("REDIG_DAYS", "Remind to re-dig tracked niches after (days)", "Test videos", "int"),
    Setting("RADAR_INTERVAL_S", "Radar: starting gap between ticks (seconds)", "Radar", "int", "Then the app speeds up while Google allows it and doubles the gap when blocked"),
    Setting("RADAR_MIN_INTERVAL_S", "Radar: fastest (seconds/tick)", "Radar", "int", "60 seconds ≈ 1,440 ticks/day"),
    Setting("RADAR_SPEEDUP_EVERY", "Radar: speed up after (unblocked ticks in a row)", "Radar", "int"),
    Setting("RADAR_SPEEDUP_PCT", "Radar: each speed-up is faster by (%)", "Radar", "int"),
    Setting("RADAR_EMPTY_RESCAN_DAYS", "Radar: rescan pairs with no data after (days)", "Radar", "int"),
    Setting("RADAR_BARREN_RESCAN_DAYS", "Radar: rescan pairs with no passing keyword (after 3 scans) after (days)", "Radar", "int"),
    Setting("RADAR_PER_SOURCE", "Radar: new keywords checked per category", "Radar", "int"),
    Setting("RADAR_MIN_RISING", "Radar: only consider rising keywords from (%)", "Radar", "int"),
    Setting("RADAR_YT_UNITS_PER_DAY", "Radar: YouTube quota for scoring keywords per day (units)", "Radar", "int", "≈ 102 units per keyword"),
    Setting("RADAR_30D_MAX_ZERO_SHARE", "Radar: exclude when zero days in the last 30 exceed (share)", "Radar", "float", "YouTube Search and Google Search checked separately; 0.5 = more than half of the days"),
    Setting("RADAR_30D_MIN_AVG", "Radar: exclude when the 30-day average is under (0–100 scale)", "Radar", "float", "Relative to the highest day in the 30 days; low = on-and-off, few searches"),
    Setting("RADAR_GOOD_MIN", "Radar: mark ⭐ from potential score", "Radar", "float"),
    Setting("RADAR_WEIGHTS", "Radar: potential score weights", "Radar", "weights", "Momentum, 30-day steadiness, YouTube, days seen; must add up to 1"),
    Setting("TEST_STANDOUT_RATIO", "Standout test video: views (×) the median", "Test videos", "float"),
    Setting("TEST_CTR_LOW", "Low CTR: under (%)", "Test videos", "float"),
    Setting("TEST_RETENTION_LOW", "Low retention: under (% of duration)", "Test videos", "float"),
    Setting("TEST_IMPRESSIONS_LOW", "Almost no impressions: under (impressions, after 7 days)", "Test videos", "int"),
]

DEFAULTS = {s.key: copy.deepcopy(getattr(cfg, s.key)) for s in EDITABLE}
_BY_KEY = {s.key: s for s in EDITABLE}


def load_overrides() -> dict:
    try:
        return json.loads(cfg.SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _coerce(setting: Setting, value):
    if setting.kind == "int":
        return int(value)
    if setting.kind == "float":
        return float(value)
    if setting.kind == "pair":
        return (float(value[0]), float(value[1]))
    return {k: float(v) for k, v in value.items()}


def validate(values: dict) -> list[str]:
    errors = []
    for key in ("NICHE_WEIGHTS", "KEYWORD_COMPETITION_WEIGHTS", "RADAR_WEIGHTS"):
        w = values.get(key)
        if w and abs(sum(w.values()) - 1) > 1e-6:
            errors.append(f"{_BY_KEY[key].label} must add up to 1 (currently {sum(w.values()):.2f})")
    for key in ("DEMAND_LOG_RANGE", "FRESHNESS_VPH_LOG_RANGE", "KEYWORD_DEMAND_LOG_RANGE", "OPPORTUNITY_RPM_LOG_RANGE"):
        if key in values and values[key][0] >= values[key][1]:
            errors.append(f"{_BY_KEY[key].label}: the 0-point mark must be below the 100-point mark")
    order = [values.get(k, getattr(cfg, k)) for k in ("OUTLIER_OUTPERFORM", "OUTLIER_VIRAL", "OUTLIER_SUPER_VIRAL")]
    if not order[0] < order[1] < order[2]:
        errors.append("Outlier thresholds must increase: outperformer < viral < super viral")
    return errors


def apply(overrides: dict) -> None:
    """Reset to defaults, then apply the saved overrides to the config module."""
    for key, value in DEFAULTS.items():
        setattr(cfg, key, copy.deepcopy(value))
    for key, value in overrides.items():
        if key in _BY_KEY:
            setattr(cfg, key, _coerce(_BY_KEY[key], value))


def save(values: dict) -> None:
    """Save only the values that differ from the defaults."""
    overrides = {k: v for k, v in values.items() if k in _BY_KEY and _coerce(_BY_KEY[k], v) != DEFAULTS[k]}
    cfg.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg.SETTINGS_PATH.write_text(json.dumps(overrides, ensure_ascii=False, indent=1), encoding="utf-8")
    apply(overrides)


def init() -> None:
    apply(load_overrides())
