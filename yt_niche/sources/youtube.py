"""Async client for YouTube Data API v3: parallel calls, SQLite cache, quota logging."""

from __future__ import annotations

import asyncio
import sqlite3
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator

import httpx

from yt_niche import config
from yt_niche.config import (
    TTL_CHANNEL_MEDIAN,
    TTL_CHANNELS,
    TTL_COMMENTS,
    TTL_SEARCH,
    TTL_VIDEOS,
    YOUTUBE_CONCURRENCY,
    YOUTUBE_UNIT_COST,
)
from yt_niche.core.formats import LONG, SHORTS, classify, parse_duration
from yt_niche.db import cache, quota

BASE_URL = "https://www.googleapis.com/youtube/v3"
MAX_IDS_PER_CALL = 50
# Number of the channel's most recent videos used for the median (1 playlistItems page).
CHANNEL_SAMPLE_SIZE = 50


class YouTubeError(RuntimeError):
    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class QuotaExceeded(YouTubeError):
    pass


@dataclass
class QuotaMeter:
    """Counts units spent and cache hits within one session."""

    units: Counter = field(default_factory=Counter)
    calls: Counter = field(default_factory=Counter)
    cache_hits: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.units.values())


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class YouTubeClient:
    """Use as `async with YouTubeClient(conn) as yt:`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        api_key: str | None = None,
        concurrency: int = YOUTUBE_CONCURRENCY,
        transport: httpx.AsyncBaseTransport | None = None,
        fresh: bool = False,
    ):
        """fresh=True: skip the cache when reading (still writes it), used for "Re-dig"."""
        api_key = api_key or config.api_key()
        if not api_key:
            raise YouTubeError("Missing YouTube API key (YOUTUBE_API_KEY or YT_API_KEY in .env / youtube.env)")
        self.conn = conn
        self.api_key = api_key
        self.meter = QuotaMeter()
        self.fresh = fresh
        self._sem = asyncio.Semaphore(concurrency)
        self._http = httpx.AsyncClient(timeout=30, transport=transport)

    async def __aenter__(self) -> "YouTubeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _get(self, endpoint: str, params: dict, ttl: float) -> dict:
        key = cache.make_key(endpoint, params)
        if ttl and not self.fresh and (hit := cache.get(self.conn, key)) is not None:
            self.meter.cache_hits[endpoint] += 1
            return hit

        async with self._sem:
            # YouTube charges quota even for failed requests, so record before calling.
            cost = YOUTUBE_UNIT_COST[endpoint]
            self.meter.units[endpoint] += cost
            self.meter.calls[endpoint] += 1
            quota.record(self.conn, endpoint, cost)
            resp = await self._http.get(f"{BASE_URL}/{endpoint}", params={**params, "key": self.api_key})

        if resp.status_code != 200:
            reason, message = "", resp.text[:300]
            try:
                err = resp.json()["error"]
                reason = err.get("errors", [{}])[0].get("reason", "")
                message = err.get("message", message)
            except Exception:
                pass
            exc_type = QuotaExceeded if reason in ("quotaExceeded", "dailyLimitExceeded") else YouTubeError
            raise exc_type(f"{endpoint} error {resp.status_code} {reason}: {message}", reason)

        data = resp.json()
        if ttl:
            cache.put(self.conn, key, data, ttl)
        return data

    async def search_videos(
        self,
        query: str,
        *,
        region_code: str,
        language: str,
        published_after: str,
        video_duration: str = "any",
        order: str = "relevance",
        pages: int = 1,
    ) -> list[str]:
        """Video ids in result order (at most 50/page, 100 units/page)."""
        ids: list[str] = []
        page_token = None
        for _ in range(pages):
            params = {
                "part": "id",
                "type": "video",
                "q": query,
                "maxResults": 50,
                "regionCode": region_code,
                "relevanceLanguage": language,
                "publishedAfter": published_after,
                "videoDuration": video_duration,
                "order": order,
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._get("search", params, TTL_SEARCH)
            ids += [item["id"]["videoId"] for item in data.get("items", [])]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return list(dict.fromkeys(ids))

    async def _batched(self, endpoint: str, part: str, ids: list[str], ttl: float) -> list[dict]:
        pages = await asyncio.gather(
            *(
                self._get(endpoint, {"part": part, "id": ",".join(chunk), "maxResults": 50}, ttl)
                for chunk in _chunks(ids, MAX_IDS_PER_CALL)
            )
        )
        return [item for page in pages for item in page.get("items", [])]

    async def videos(self, video_ids: list[str], ttl: float = TTL_VIDEOS) -> list[dict]:
        return await self._batched("videos", "snippet,statistics,contentDetails", video_ids, ttl)

    async def channels(self, channel_ids: list[str], ttl: float = TTL_CHANNELS) -> list[dict]:
        return await self._batched("channels", "snippet,statistics,contentDetails", channel_ids, ttl)

    async def channels_branding(self, channel_ids: list[str], ttl: float = TTL_CHANNELS) -> list[dict]:
        """Like channels() but with brandingSettings (channel tags). Still 1 unit / 50 channels."""
        return await self._batched("channels", "snippet,statistics,contentDetails,brandingSettings", channel_ids, ttl)

    async def channel_by_handle(self, handle: str) -> str | None:
        """@handle → channel id (1 unit)."""
        data = await self._get("channels", {"part": "id", "forHandle": handle}, TTL_CHANNELS)
        items = data.get("items", [])
        return items[0]["id"] if items else None

    async def recent_uploads(self, uploads_playlist_id: str, n: int = 15, ttl: float = TTL_CHANNELS) -> list[dict]:
        """[{video_id, title}] of the n newest videos (1 unit, cached). A channel without public videos → []."""
        try:
            data = await self._get(
                "playlistItems", {"part": "snippet", "playlistId": uploads_playlist_id, "maxResults": n}, ttl
            )
        except QuotaExceeded:
            raise
        except YouTubeError:
            return []
        return [
            {"video_id": it["snippet"]["resourceId"]["videoId"], "title": it["snippet"].get("title", "")}
            for it in data.get("items", [])
            if it.get("snippet", {}).get("resourceId", {}).get("videoId")
        ]

    async def comment_threads(self, video_id: str, max_results: int = 50) -> list[dict]:
        """Most relevant top-level comments of a video (1 unit). Returns [] when comments are off."""
        try:
            data = await self._get(
                "commentThreads",
                {"part": "snippet", "videoId": video_id, "maxResults": max_results, "order": "relevance", "textFormat": "plainText"},
                TTL_COMMENTS,
            )
        except QuotaExceeded:
            raise
        except YouTubeError as e:
            if e.reason in ("commentsDisabled", "videoNotFound", "forbidden"):
                return []
            raise
        out = []
        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            out.append(
                {
                    "video_id": video_id,
                    "text": top.get("textDisplay", ""),
                    "likes": int(top.get("likeCount", 0)),
                    "replies": int(item["snippet"].get("totalReplyCount", 0)),
                }
            )
        return out

    async def i18n(self, kind: str, hl: str = "vi") -> list[dict]:
        """YouTube's official lists: kind="regions" → [{code, name}] (gl), "languages" → [{code, name}] (hl)."""
        endpoint = {"regions": "i18nRegions", "languages": "i18nLanguages"}[kind]
        data = await self._get(endpoint, {"part": "snippet", "hl": hl}, ttl=30 * 86400)
        field = "gl" if kind == "regions" else "hl"
        return sorted(({"code": it["snippet"][field], "name": it["snippet"]["name"]} for it in data.get("items", [])),
                      key=lambda x: x["name"])

    async def recent_upload_ids(self, uploads_playlist_id: str) -> list[str]:
        data = await self._get(
            "playlistItems",
            {"part": "contentDetails", "playlistId": uploads_playlist_id, "maxResults": CHANNEL_SAMPLE_SIZE},
            ttl=0,
        )
        return [item["contentDetails"]["videoId"] for item in data.get("items", [])]

    # ---- Channel median views, split by format ----

    async def channel_medians(self, channel_items: list[dict], max_age: float = TTL_CHANNEL_MEDIAN) -> dict[str, dict]:
        """Save channel info into the `channel` table and return {channel_id: row}.

        Calls the API only for channels without a median or with a median older than `max_age`.
        """
        for ch in channel_items:
            _upsert_channel_info(self.conn, ch)

        ids = [ch["id"] for ch in channel_items]
        rows = _load_channels(self.conn, ids)
        stale = [
            rows[cid]
            for cid in ids
            if rows[cid]["median_fetched_at"] is None or rows[cid]["median_fetched_at"] < time.time() - max_age
        ]
        self.meter.cache_hits["channel_median"] += len(ids) - len(stale)

        await asyncio.gather(*(self._refresh_channel_median(row) for row in stale))
        return _load_channels(self.conn, ids)

    async def _refresh_channel_median(self, row: dict) -> None:
        long_views: list[int] = []
        short_views: list[int] = []
        if row["uploads_playlist"]:
            try:
                recent_ids = await self.recent_upload_ids(row["uploads_playlist"])
                recent = await self.videos(recent_ids, ttl=0) if recent_ids else []
            except QuotaExceeded:
                raise
            except YouTubeError:
                recent = []  # e.g. playlistNotFound: the channel has no public videos
            for v in recent:
                views = v.get("statistics", {}).get("viewCount")
                duration = v.get("contentDetails", {}).get("duration")
                if views is None or not duration or v["snippet"].get("liveBroadcastContent", "none") != "none":
                    continue
                fmt = classify(parse_duration(duration))
                if fmt == LONG:
                    long_views.append(int(views))
                elif fmt == SHORTS:
                    short_views.append(int(views))

        self.conn.execute(
            """UPDATE channel SET median_long = ?, n_long = ?, median_short = ?, n_short = ?, median_fetched_at = ?
               WHERE channel_id = ?""",
            (
                statistics.median(long_views) if long_views else None,
                len(long_views),
                statistics.median(short_views) if short_views else None,
                len(short_views),
                time.time(),
                row["channel_id"],
            ),
        )


def _upsert_channel_info(conn: sqlite3.Connection, ch: dict) -> None:
    stats = ch.get("statistics", {})
    hidden = bool(stats.get("hiddenSubscriberCount"))
    conn.execute(
        """INSERT INTO channel (channel_id, title, subs, subs_hidden, created_at, video_count, uploads_playlist)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(channel_id) DO UPDATE SET
               title = excluded.title, subs = excluded.subs, subs_hidden = excluded.subs_hidden,
               created_at = excluded.created_at, video_count = excluded.video_count,
               uploads_playlist = excluded.uploads_playlist""",
        (
            ch["id"],
            ch.get("snippet", {}).get("title"),
            None if hidden else int(stats.get("subscriberCount", 0)),
            int(hidden),
            ch.get("snippet", {}).get("publishedAt"),
            int(stats["videoCount"]) if "videoCount" in stats else None,
            ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
        ),
    )


def _load_channels(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM channel WHERE channel_id IN ({placeholders})", ids).fetchall()
    return {r["channel_id"]: dict(r) for r in rows}
