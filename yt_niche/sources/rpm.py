"""Bảng RPM ước tính (USD / 1.000 view) theo thị trường × nhóm chủ đề, lưu ở data/rpm_table.csv.

Số khởi đầu là ƯỚC TÍNH THÔ để xếp hạng ngách, không phải số liệu thật. Hãy sửa dần theo số của bạn.
"""

from __future__ import annotations

import pandas as pd

from yt_niche import config as cfg

COLUMNS = ["market", "category", "rpm_low", "rpm_high"]

# RPM khởi đầu cho thị trường VN theo nhóm chủ đề (thấp, cao).
_VN_BASE = {
    "Giải trí / kể chuyện": (0.3, 0.6),
    "Lịch sử": (0.4, 0.8),
    "Giáo dục": (0.5, 1.0),
    "Tài chính / kinh doanh": (1.0, 2.0),
    "Công nghệ": (0.6, 1.2),
    "Sức khỏe": (0.5, 1.0),
    "Tâm lý / phát triển bản thân": (0.5, 1.0),
    "Tâm linh / tôn giáo": (0.3, 0.7),
    "Nấu ăn": (0.4, 0.8),
    "Du lịch": (0.4, 0.8),
    "Game": (0.2, 0.5),
    "Thiếu nhi": (0.2, 0.4),
    "Âm nhạc / thư giãn": (0.2, 0.5),
}

# Hệ số so với VN (bài 2.1: khán giả Mỹ trả khoảng 4 lần VN).
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
    # Bảng cũ thiếu thị trường mới thêm: bổ sung số mặc định, giữ nguyên các dòng đã sửa.
    missing = sorted(set(_MARKET_FACTOR) - set(df["market"]))
    if missing:
        default = default_table()
        df = pd.concat([df, default[default["market"].isin(missing)]], ignore_index=True)
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
