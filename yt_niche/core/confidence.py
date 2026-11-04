"""Confidence of the sample (lesson 2.2)."""

from __future__ import annotations

from yt_niche import config as cfg

HIGH, MEDIUM, LOW = "high", "medium", "low"


def confidence(n: int, n_estimated: int) -> str:
    share = n_estimated / n if n else 1.0
    if n < cfg.CONFIDENCE_LOW_MAX_N or share > cfg.CONFIDENCE_LOW_MIN_ESTIMATED:
        return LOW
    if n >= cfg.CONFIDENCE_HIGH_MIN_N and share <= cfg.CONFIDENCE_HIGH_MAX_ESTIMATED:
        return HIGH
    return MEDIUM
