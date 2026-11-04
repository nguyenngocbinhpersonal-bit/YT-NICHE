"""Collect all raw data for one niche dig (no scoring yet)."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from yt_niche.core.formats import classify, parse_duration
from yt_niche.sources.trends import get_trends
from yt_niche.sources.youtube import YouTubeClient

# App format → API videoDuration ("short" = under 4 minutes, then filtered again to ≤ 180s).
FORMAT_TO_API = {"any": "any", "long": "any", "shorts": "short"}


@dataclass
class DigParams:
    topic: str
    region: str = "VN"
    language: str = "vi"
    window_days: int = 30
    fmt: str = "any"  # any | long | shorts
    order: str = "relevance"  # relevance | viewCount
    pages: int = 2
    with_trends: bool = True
    trends_source: str = "youtube"  # youtube | web


def published_after(window_days: int, now: datetime | None = None) -> str:
    """Rounded down to 00:00 UTC so the same day yields the same cache key."""
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=window_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_video(v: dict) -> dict | None:
    snippet, stats, details = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
    # Skip live / upcoming streams: no duration yet and views are not settled.
    if snippet.get("liveBroadcastContent", "none") != "none" or not details.get("duration"):
        return None
    seconds = parse_duration(details["duration"])
    return {
        "video_id": v["id"],
        "channel_id": snippet["channelId"],
        "title": snippet.get("title", ""),
        "published_at": snippet.get("publishedAt"),
        "duration_s": seconds,
        "fmt": classify(seconds),
        "views": int(stats["viewCount"]) if "viewCount" in stats else None,
        "likes": int(stats["likeCount"]) if "likeCount" in stats else None,
        "comments": int(stats["commentCount"]) if "commentCount" in stats else None,
    }


async def fetch_niche(conn: sqlite3.Connection, params: DigParams, fresh: bool = False) -> dict:
    """fresh=True: fetch new numbers (skip the search/videos/channels/Trends cache; channel medians still use the 7-day cache)."""
    t0 = time.perf_counter()
    async with YouTubeClient(conn, fresh=fresh) as yt:
        trends_task = (
            asyncio.create_task(get_trends(conn, params.topic, params.region, params.trends_source, fresh=fresh))
            if params.with_trends
            else None
        )
        try:
            ids = await yt.search_videos(
                params.topic,
                region_code=params.region,
                language=params.language,
                published_after=published_after(params.window_days),
                video_duration=FORMAT_TO_API[params.fmt],
                order=params.order,
                pages=params.pages,
            )
            videos = [n for v in await yt.videos(ids) if (n := _normalize_video(v))]
            channel_ids = list(dict.fromkeys(v["channel_id"] for v in videos))
            channel_items = await yt.channels(channel_ids)
            channels = await yt.channel_medians(channel_items)
        except BaseException:
            if trends_task:
                trends_task.cancel()
            raise
        trends = await trends_task if trends_task else None

    return {
        "params": asdict(params),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "videos": videos,
        "channels": list(channels.values()),
        "trends": trends,
        "quota": {
            "units": yt.meter.total,
            "by_endpoint": dict(yt.meter.units),
            "calls": dict(yt.meter.calls),
            "cache_hits": dict(yt.meter.cache_hits),
        },
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
