"""Video duration and Long-form / Shorts classification."""

from __future__ import annotations

import re

from yt_niche import config as cfg

LONG, SHORTS, MID = "long", "shorts", "mid"

_DURATION_RE = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?"
)


def parse_duration(iso: str) -> int:
    """ISO 8601 duration (e.g. PT1H2M3S) to seconds; 0 when it cannot be parsed."""
    match = _DURATION_RE.fullmatch(iso or "")
    if not match:
        return 0
    parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def classify(seconds: int) -> str:
    """Shorts ≤ 180s, long-form ≥ 240s, in between is 'mid' (left out of comparisons)."""
    if seconds <= cfg.SHORTS_MAX_SECONDS:
        return SHORTS
    if seconds >= cfg.LONG_MIN_SECONDS:
        return LONG
    return MID
