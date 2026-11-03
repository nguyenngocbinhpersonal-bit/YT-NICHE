"""Điểm cơ hội đa thị trường (bài 2.4).

- Có Google Trends của nước đó: 45% Điểm ngách + 30% nhu cầu tìm kiếm + 25% khả năng kiếm tiền.
- Chưa có Trends: 60% Điểm ngách + 40% giá quảng cáo.
- Từ 65 trở lên là vùng xanh.

Cách quy "nhu cầu tìm kiếm" và "giá quảng cáo" về thang 0–100 là giả định của app:
- Nhu cầu tìm kiếm = mức quan tâm trung bình 12 tháng gần nhất so với mức cao (phân vị 90) của 5 năm.
  Trends chỉ cho số tương đối; dùng phân vị 90 thay cho đỉnh để chủ đề theo mùa không bị phạt vì một tuần đỉnh.
- Giá quảng cáo = RPM trung bình theo thang log (OPPORTUNITY_RPM_LOG_RANGE).
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
        return None  # dữ liệu thưa: coi như chưa có Trends
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
        return {"score": None, "formula": "chưa có RPM (chọn nhóm chủ đề)", "money": None, "search": search}
    if search is not None:
        w = cfg.OPPORTUNITY_WEIGHTS
        score = w["niche"] * niche_score + w["search"] * search + w["money"] * money
        formula = f"{w['niche']:.0%} điểm ngách + {w['search']:.0%} tìm kiếm + {w['money']:.0%} kiếm tiền"
    else:
        w = cfg.OPPORTUNITY_WEIGHTS_NO_TRENDS
        score = w["niche"] * niche_score + w["money"] * money
        formula = f"{w['niche']:.0%} điểm ngách + {w['money']:.0%} giá quảng cáo (chưa có Trends)"
    score = round(score, 1)
    return {"score": score, "green": score >= cfg.OPPORTUNITY_GREEN_MIN, "formula": formula, "money": money, "search": search}


def winning_topics(outliers: list[dict], keyword: str, top: int = 5) -> list[str]:
    """Chủ đề con thắng ở một nước: các từ lặp lại nhiều nhất trong tiêu đề video vượt trội (bỏ từ của từ khoá)."""
    from yt_niche.core.subniche import repeated_phrases

    phrases = repeated_phrases(outliers, keyword, min_channels=2, top=top)
    if phrases:
        return [p["phrase"] for p in phrases]
    # Ít video quá thì trả về vài tiêu đề đầu làm ví dụ.
    kw = set(tokens(keyword))
    return [v["title"] for v in outliers[:3] if set(tokens(v["title"])) - kw]
