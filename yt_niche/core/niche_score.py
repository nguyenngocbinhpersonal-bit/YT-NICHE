"""Niche score 0–100 from four bars (lesson 2.2) and the list of small booming channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from yt_niche import config as cfg
from yt_niche.core.stats import log_scale, median, months_between, parse_iso

LABEL_GREEN = "Green niche"
LABEL_POTENTIAL = "Promising niche"
LABEL_RED = "Red niche"


@dataclass
class NicheScore:
    n: int
    median_views: float | None
    demand: float
    opportunity: float
    freshness: float
    competition: float
    score: float
    label: str


def niche_label(score: float) -> str:
    if score >= cfg.LABEL_GREEN_MIN:
        return LABEL_GREEN
    if score >= cfg.LABEL_POTENTIAL_MIN:
        return LABEL_POTENTIAL
    return LABEL_RED


def is_small(subs: int | None) -> bool:
    return subs is not None and subs < cfg.SMALL_CHANNEL_MAX_SUBS


def demand_bar(sample: list[dict]) -> tuple[float, float | None]:
    med = median(v["views"] for v in sample)
    return log_scale(med, *cfg.DEMAND_LOG_RANGE), med


def opportunity_bar(sample: list[dict]) -> float:
    """% of outperformer videos (≥ 3×) that come from small channels."""
    outliers = [v for v in sample if v["level"]]
    if not outliers:
        return 0.0
    return sum(is_small(v["subs"]) for v in outliers) / len(outliers) * 100


def freshness_bar(sample: list[dict], window_days: int, now: datetime) -> float:
    if not sample:
        return 0.0
    if window_days > cfg.FRESHNESS_DAYS:
        cutoff = now - timedelta(days=cfg.FRESHNESS_DAYS)
        return sum(parse_iso(v["published_at"]) >= cutoff for v in sample) / len(sample) * 100
    # The sample already holds only videos ≤ 30 days old: measure by median views per hour.
    vph = median(
        v["views"] / max(1.0, (now - parse_iso(v["published_at"])).total_seconds() / 3600)
        for v in sample
        if v["views"] is not None
    )
    return log_scale(vph, *cfg.FRESHNESS_VPH_LOG_RANGE)


def competition_bar(sample: list[dict]) -> float:
    """% of videos from channels > 1M subs (the higher, the more points lost)."""
    if not sample:
        return 0.0
    return sum(v["subs"] is not None and v["subs"] > cfg.BIG_CHANNEL_MIN_SUBS for v in sample) / len(sample) * 100


def niche_score(sample: list[dict], window_days: int, now: datetime) -> NicheScore:
    demand, med = demand_bar(sample)
    opportunity = opportunity_bar(sample)
    freshness = freshness_bar(sample, window_days, now)
    competition = competition_bar(sample)
    w = cfg.NICHE_WEIGHTS
    score = (
        w["demand"] * demand
        + w["opportunity"] * opportunity
        + w["freshness"] * freshness
        + w["competition"] * (100 - competition)
    )
    return NicheScore(
        n=len(sample),
        median_views=med,
        demand=round(demand, 1),
        opportunity=round(opportunity, 1),
        freshness=round(freshness, 1),
        competition=round(competition, 1),
        score=round(score, 1),
        label=niche_label(score),
    )


def small_boom_channels(sample: list[dict], channels: dict[str, dict], now: datetime) -> list[dict]:
    """Channels < 100K subs with at least one outperformer video in the sample; young channels first."""
    best: dict[str, dict] = {}
    for v in sample:
        if v["level"] and is_small(v["subs"]):
            if v["channel_id"] not in best or v["outlier"] > best[v["channel_id"]]["outlier"]:
                best[v["channel_id"]] = v

    cards = []
    for cid, v in best.items():
        ch = channels.get(cid, {})
        age = months_between(parse_iso(ch["created_at"]), now) if ch.get("created_at") else None
        cards.append(
            {
                "channel_id": cid,
                "title": ch.get("title"),
                "subs": v["subs"],
                "age_months": age,
                "young": age is not None and age < cfg.YOUNG_CHANNEL_MONTHS,
                "video_count": ch.get("video_count"),
                "best_video_id": v["video_id"],
                "best_title": v["title"],
                "best_views": v["views"],
                "best_outlier": round(v["outlier"], 1),
            }
        )
    return sorted(cards, key=lambda c: (not c["young"], -c["best_outlier"]))


SIZE_SMALL, SIZE_MID, SIZE_BIG, SIZE_HIDDEN = "small", "medium", "big", "hidden subs"


def channel_size(subs: int | None) -> str:
    if subs is None:
        return SIZE_HIDDEN
    return SIZE_SMALL if is_small(subs) else SIZE_BIG if subs >= cfg.BIG_CHANNEL_MIN_SUBS else SIZE_MID


def all_channels(data: dict) -> list[dict]:
    """Every channel with a video in the keyword search results (raw data of a dig), sorted by total views in the sample."""
    from yt_niche.core.outlier import enrich_videos

    now = parse_iso(data["fetched_at"])
    channels = {c["channel_id"]: c for c in data.get("channels") or []}
    by_channel: dict[str, list[dict]] = {}
    for v in enrich_videos(data.get("videos") or [], channels):
        by_channel.setdefault(v["channel_id"], []).append(v)

    rows = []
    for cid, vids in by_channel.items():
        ch = channels.get(cid, {})
        best = max(vids, key=lambda v: v["views"] or 0)
        ratios = [v["outlier"] for v in vids if v["outlier"] is not None]
        subs = None if ch.get("subs_hidden") else ch.get("subs")
        rows.append(
            {
                "channel_id": cid,
                "title": ch.get("title") or cid,
                "subs": subs,
                "size": channel_size(subs),
                "age_months": months_between(parse_iso(ch["created_at"]), now) if ch.get("created_at") else None,
                "video_count": ch.get("video_count"),
                "n_videos": len(vids),
                "total_views": sum(v["views"] or 0 for v in vids),
                "n_outliers": sum(bool(v["level"]) for v in vids),
                "best_outlier": round(max(ratios), 1) if ratios else None,
                "best_video_id": best["video_id"],
                "best_title": best["title"],
                "best_views": best["views"],
            }
        )
    return sorted(rows, key=lambda r: r["total_views"], reverse=True)
