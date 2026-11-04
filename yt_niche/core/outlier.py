"""Outlier: video views ÷ the channel's own median views (same format)."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.formats import LONG, SHORTS

LEVEL_OUTPERFORM = "Outperformer"
LEVEL_VIRAL = "Viral"
LEVEL_SUPER_VIRAL = "Super viral"


def outlier_level(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio >= cfg.OUTLIER_SUPER_VIRAL:
        return LEVEL_SUPER_VIRAL
    if ratio >= cfg.OUTLIER_VIRAL:
        return LEVEL_VIRAL
    if ratio >= cfg.OUTLIER_OUTPERFORM:
        return LEVEL_OUTPERFORM
    return None


def channel_median_for(channel: dict | None, fmt: str) -> float | None:
    """Channel median for the matching format; None when missing or the channel sample is too small to trust."""
    if not channel:
        return None
    if fmt == LONG:
        med, n = channel.get("median_long"), channel.get("n_long", 0)
    elif fmt == SHORTS:
        med, n = channel.get("median_short"), channel.get("n_short", 0)
    else:
        return None
    return med if med and n >= cfg.MIN_CHANNEL_SAMPLE else None


def enrich_videos(videos: list[dict], channels: dict[str, dict]) -> list[dict]:
    """Add channel_median, outlier, level, subs and the estimated flag to each video."""
    out = []
    for v in videos:
        ch = channels.get(v["channel_id"])
        med = channel_median_for(ch, v["fmt"])
        subs = None if not ch or ch.get("subs_hidden") else ch.get("subs")
        ratio = v["views"] / med if v["views"] is not None and med else None
        out.append(
            {
                **v,
                "subs": subs,
                "channel_median": med,
                "outlier": ratio,
                "level": outlier_level(ratio),
                # "Estimated" number: views missing, subs hidden, or the channel median cannot be trusted.
                "estimated": v["views"] is None or subs is None or med is None,
            }
        )
    return out
