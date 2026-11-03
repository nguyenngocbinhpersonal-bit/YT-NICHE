"""Phân loại Google Trends: bền / trào lưu / đang rơi / theo mùa (bài 2.2)."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from yt_niche import config as cfg

STABLE, FAD, FALLING, SEASONAL = "bền", "trào lưu", "đang rơi", "theo mùa"

ADVICE = {
    STABLE: "Cầu bền: có thể xây cả kênh.",
    FAD: "Trào lưu: làm nhanh vài video, không xây cả kênh.",
    FALLING: "Đang rơi: chỉ vào nếu điểm vẫn cao, cần góc mới và ngách dự phòng.",
    SEASONAL: "Theo mùa: chuẩn bị video trước đỉnh 4–6 tuần.",
}

WEEKS_PER_YEAR = 52
RECENT_WEEKS = 13


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    avg, x_mean = mean(values), (n - 1) / 2
    return sum((i - x_mean) * (v - avg) for i, v in enumerate(values)) / sum((i - x_mean) ** 2 for i in range(n))


def relative_trend(values: list[float]) -> float:
    """Độ thay đổi của đường hồi quy tuyến tính từ đầu đến cuối chuỗi, chia cho trung bình."""
    avg = mean(values) if values else 0
    if len(values) < 2 or avg == 0:
        return 0.0
    return _slope(values) * (len(values) - 1) / avg


def detrend(values: list[float]) -> list[float]:
    """Trừ đường xu hướng tuyến tính, để chuỗi đang lên/xuống không bị nhầm là theo mùa."""
    slope, avg, x_mean = _slope(values), mean(values), (len(values) - 1) / 2
    return [v - (avg + slope * (i - x_mean)) for i, v in enumerate(values)]


def autocorrelation(values: list[float], lag: int) -> float:
    n = len(values)
    if n <= lag + 1:
        return 0.0
    avg = mean(values)
    denom = sum((v - avg) ** 2 for v in values)
    if denom == 0:
        return 0.0
    return sum((values[i] - avg) * (values[i + lag] - avg) for i in range(n - lag)) / denom


def peak_month(points: list[list]) -> int:
    """Tháng (1–12) có giá trị trung bình cao nhất qua các năm."""
    by_month: dict[int, list[float]] = defaultdict(list)
    for date, value in points:
        by_month[int(date[5:7])].append(value)
    return max(by_month, key=lambda m: mean(by_month[m]))


def classify_trends(series: dict[str, list[list]] | None) -> dict:
    """series = {"5y": [[ngày, giá trị], ...], "12m": [...], "3m": [...]} (theo tuần cho 5y/12m)."""
    points_5y = (series or {}).get("5y") or []
    if len(points_5y) < WEEKS_PER_YEAR * 2:
        return {"class": None, "reason": "chưa có đủ dữ liệu Trends 5 năm", "sparse": True}

    values = [float(v) for _, v in points_5y]
    sparse = sum(v == 0 for v in values) / len(values) > cfg.TRENDS_SPARSE_ZERO_SHARE
    base = mean(values[: -WEEKS_PER_YEAR]) if len(values) > WEEKS_PER_YEAR else mean(values)
    recent = mean(values[-RECENT_WEEKS:])
    rel_5y = relative_trend(values)
    values_12m = [float(v) for _, v in (series.get("12m") or [])] or values[-WEEKS_PER_YEAR:]
    rel_12m = relative_trend(values_12m)
    ac = autocorrelation(detrend(values), WEEKS_PER_YEAR)

    metrics = {
        "base_mean": round(base, 1),
        "recent_mean": round(recent, 1),
        "trend_5y": round(rel_5y, 2),
        "trend_12m": round(rel_12m, 2),
        "autocorr_52w": round(ac, 2),
    }

    direction = "đi lên" if rel_5y > 0.1 else "đi ngang" if rel_5y > cfg.TRENDS_FALLING_5Y else "đi xuống"
    if base <= cfg.TRENDS_FAD_BASE_MAX and recent >= cfg.TRENDS_FAD_RECENT_MIN and recent >= cfg.TRENDS_FAD_RATIO * max(base, 1):
        cls, reason = FAD, f"nền 4 năm ≈ {base:.1f}, 13 tuần gần nhất ≈ {recent:.1f}"
    elif ac >= cfg.TRENDS_SEASONAL_AUTOCORR:
        cls = SEASONAL
        reason = (
            f"lặp lại theo năm (tự tương quan 52 tuần = {ac:.2f}), đỉnh tháng {peak_month(points_5y)}, "
            f"nền 5 năm {direction} ({rel_5y:+.0%})"
        )
    elif rel_5y <= cfg.TRENDS_FALLING_5Y and rel_12m < 0:
        cls, reason = FALLING, f"5 năm {rel_5y:+.0%}, 12 tháng {rel_12m:+.0%}"
    else:
        cls, reason = STABLE, f"5 năm {direction} ({rel_5y:+.0%}), 12 tháng {rel_12m:+.0%}"

    if sparse:
        reason += "; dữ liệu thưa (nhiều tuần bằng 0), cần xem biểu đồ bằng mắt"
    result = {"class": cls, "direction": direction, "reason": reason, "advice": ADVICE[cls], "sparse": sparse, **metrics}
    if cls == SEASONAL:
        result["peak_month"] = peak_month(points_5y)
    return result
