"""Thời lượng video và phân loại định dạng Dài / Shorts."""

from __future__ import annotations

import re

from yt_niche import config as cfg

LONG, SHORTS, MID = "long", "shorts", "mid"

_DURATION_RE = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?"
)


def parse_duration(iso: str) -> int:
    """Đổi thời lượng ISO 8601 (vd. PT1H2M3S) ra số giây; 0 nếu không đọc được."""
    match = _DURATION_RE.fullmatch(iso or "")
    if not match:
        return 0
    parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def classify(seconds: int) -> str:
    """Shorts ≤ 180s, video dài ≥ 240s, khoảng giữa là 'mid' (không đưa vào phép so)."""
    if seconds <= cfg.SHORTS_MAX_SECONDS:
        return SHORTS
    if seconds >= cfg.LONG_MIN_SECONDS:
        return LONG
    return MID
