"""Hàm thống kê nhỏ dùng chung."""

from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Iterable


def median(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def log_scale(value: float | None, lo_exp: float, hi_exp: float) -> float:
    """Đổi value sang 0–100 theo log10: 10**lo_exp → 0, 10**hi_exp → 100."""
    if not value or value <= 0:
        return 0.0
    return clamp((math.log10(value) - lo_exp) / (hi_exp - lo_exp) * 100)


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def months_between(start: datetime, end: datetime) -> int:
    return max(0, (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day))


def slope(values: list[float]) -> float:
    """Hệ số góc hồi quy tuyến tính theo thứ tự phần tử (0 nếu < 2 điểm)."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean, y_mean = (n - 1) / 2, sum(values) / n
    return sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values)) / sum((i - x_mean) ** 2 for i in range(n))
