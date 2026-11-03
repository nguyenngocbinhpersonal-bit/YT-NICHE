"""Chấm điểm từ khoá dài 0–100 từ kết quả YouTube thật (bài 2.3): Nhu cầu và Cạnh tranh.

Công thức là giả định của app (khóa học không công bố):
- Nhu cầu = trung vị lượt xem các video kết quả, thang log (KEYWORD_DEMAND_LOG_RANGE).
- Cạnh tranh = % kết quả từ kênh lớn (≥ KEYWORD_BIG_CHANNEL_SUBS) và % tiêu đề nhắm đúng từ khoá, có trọng số.
- Điểm = 50% Nhu cầu + 50% (100 − Cạnh tranh).
"""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.stats import log_scale, median
from yt_niche.core.text import contains_all

VERY_GOOD, GOOD, AVERAGE, WEAK = "Rất tốt", "Tốt", "Trung bình (dự trữ)", "Yếu"
LOW_DATA = "ít dữ liệu"


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
    """results: video kết quả tìm kiếm [{"title", "views", "subs", "channel_id"}] (subs None nếu kênh ẩn sub)."""
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
    """Gợi ý ô "Từ khoá dài" của bảng chấm (bài 2.5): 5 = có từ 10 từ khoá dài đạt ≥ 55, không mang nhãn ít dữ liệu."""
    if not scored:
        return None, "chưa chấm từ khoá dài (trang Ngách con & từ khoá)"
    good = [k for k in scored if k["score"] is not None and k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]]
    n = len(good)
    score = 5 if n >= 10 else 4 if n >= 7 else 3 if n >= 4 else 2 if n >= 1 else 1
    return score, f"{n} từ khoá dài đạt ≥ {cfg.KEYWORD_GOOD} (đã chấm {len(scored)} từ)"
