"""Estimated RPM table (USD per 1,000 views) by market × topic category, stored in data/rpm_table.csv.

The starting numbers are ROUGH ESTIMATES for ranking niches, not real figures. Adjust them over time with your own numbers.
"""

from __future__ import annotations

import pandas as pd

from yt_niche import config as cfg

COLUMNS = ["market", "category", "rpm_low", "rpm_high"]

# Starting RPM for the VN market by topic category (low, high).
_VN_BASE = {
    "Entertainment / storytelling": (0.3, 0.6),
    "History": (0.4, 0.8),
    "Education": (0.5, 1.0),
    "Finance / business": (1.0, 2.0),
    "Technology": (0.6, 1.2),
    "Health": (0.5, 1.0),
    "Psychology / self-improvement": (0.5, 1.0),
    "Spirituality / religion": (0.3, 0.7),
    "Cooking": (0.4, 0.8),
    "Travel": (0.4, 0.8),
    "Gaming": (0.2, 0.5),
    "Kids": (0.2, 0.4),
    "Music / relaxation": (0.2, 0.5),
}

# Legacy (Vietnamese) category names → current names, so a user-edited CSV from before the migration keeps working.
LEGACY_CATEGORIES = {
    "Giải trí / kể chuyện": "Entertainment / storytelling",
    "Lịch sử": "History",
    "Giáo dục": "Education",
    "Tài chính / kinh doanh": "Finance / business",
    "Công nghệ": "Technology",
    "Du lịch": "Travel",
    "Game": "Gaming",
    "Nấu ăn": "Cooking",
    "Sức khỏe": "Health",
    "Thiếu nhi": "Kids",
    "Tâm linh / tôn giáo": "Spirituality / religion",
    "Tâm lý / phát triển bản thân": "Psychology / self-improvement",
    "Âm nhạc / thư giãn": "Music / relaxation",
}

# Multiplier relative to VN (lesson 2.1: US audiences pay about 4× VN).
_MARKET_FACTOR = {
    "VN": 1.0, "US": 4.0, "GB": 3.5, "CA": 3.5, "AU": 3.5, "DE": 3.5, "FR": 2.5,
    "JP": 2.5, "KR": 2.0, "TW": 1.5,
    "ID": 0.6, "TH": 0.8, "PH": 0.6,
    "BR": 1.0, "MX": 0.8, "AR": 0.4, "IN": 0.5,
}


def default_table() -> pd.DataFrame:
    rows = [
        (market, category, round(lo * factor, 2), round(hi * factor, 2))
        for market, factor in _MARKET_FACTOR.items()
        for category, (lo, hi) in _VN_BASE.items()
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def load_table() -> pd.DataFrame:
    if not cfg.RPM_TABLE_PATH.exists():
        save_table(default_table())
    df = pd.read_csv(cfg.RPM_TABLE_PATH, encoding="utf-8")
    changed = False
    # Legacy Vietnamese category names: rename on read, keep the user's numbers.
    if df["category"].isin(LEGACY_CATEGORIES).any():
        df["category"] = df["category"].replace(LEGACY_CATEGORIES)
        df = df.drop_duplicates(subset=["market", "category"], keep="first").reset_index(drop=True)
        changed = True
    # An old table missing newly added markets: add the default numbers, keep the edited rows.
    missing = sorted(set(_MARKET_FACTOR) - set(df["market"]))
    if missing:
        default = default_table()
        df = pd.concat([df, default[default["market"].isin(missing)]], ignore_index=True)
        changed = True
    if changed:
        save_table(df)
    return df


def save_table(df: pd.DataFrame) -> None:
    cfg.RPM_TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df = df.dropna(subset=["market", "category"])[COLUMNS]
    df.to_csv(cfg.RPM_TABLE_PATH, index=False, encoding="utf-8")


def categories() -> list[str]:
    return sorted(load_table()["category"].unique())


def lookup(market: str, category: str | None) -> tuple[float, float] | None:
    if not category:
        return None
    df = load_table()
    row = df[(df["market"] == market) & (df["category"] == category)]
    if row.empty:
        return None
    return float(row.iloc[0]["rpm_low"]), float(row.iloc[0]["rpm_high"])
