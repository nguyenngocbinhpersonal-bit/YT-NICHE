"""YouTube's internal web endpoints (youtubei/v1): the "Up next" list and search, no API quota.

The official API dropped relatedToVideoId (8/2023), so this is the only free way to get suggested videos.
Unofficial endpoint: YouTube can change the structure or block it at any time. SQLite cache for 24h.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from typing import Any, Iterator

import httpx

from yt_niche.config import TTL_INNERTUBE
from yt_niche.db import cache

URL = "https://www.youtube.com/youtubei/v1"
CLIENT_VERSION = "2.20261020.00.00"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
CONCURRENCY = 4
SEARCH_VIDEOS = "EgIQAQ=="  # search filter: videos only
SEARCH_CHANNELS = "EgIQAg=="  # channels only
CHANNEL_VIDEOS_TAB = "EgZ2aWRlb3PyBgQKAjoA"  # the channel's Videos tab, newest first


class InnertubeError(RuntimeError):
    pass


def walk(o: Any, key: str) -> Iterator[Any]:
    """Every value of key `key` at any depth of the JSON."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(o, list):
        for i in o:
            yield from walk(i, key)


def text(x: Any) -> str:
    if not x:
        return ""
    if isinstance(x, str):
        return x
    if "content" in x:
        return x["content"]
    if "simpleText" in x:
        return x["simpleText"]
    return "".join(r.get("text", "") for r in x.get("runs", []))


_NUM = re.compile(r"([\d.,]+)\s*([KMB]?)\b", re.IGNORECASE)


def parse_count(s: str) -> int | None:
    """"151,902 views" → 151902, "16K" → 16000, "1.2M views" → 1200000 (English format, hl=en)."""
    m = _NUM.search(s or "")
    if not m:
        return None
    num, unit = m.group(1), m.group(2).upper()
    num = num.replace(",", "") if not unit else num.replace(",", ".")
    try:
        return int(float(num) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[unit])
    except ValueError:
        return None


def _channel_id(o: Any) -> str | None:
    return next((b["browseId"] for b in walk(o, "browseEndpoint") if b.get("browseId", "").startswith("UC")), None)


# ---- Result readers ----

def lockups(data: dict) -> list[dict]:
    """Videos in lockupViewModel form (the "Up next" column, a channel's Videos tab)."""
    out = []
    for lk in walk(data, "lockupViewModel"):
        if lk.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
            continue  # skip playlists, mixes
        meta = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
        rows = [
            [text(p.get("text")) for p in r.get("metadataParts", [])]
            for r in meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
            if r.get("metadataParts")
        ]
        out.append(
            {
                "video_id": lk.get("contentId"),
                "title": text(meta.get("title")),
                "channel": rows[0][0] if rows and rows[0] else "",
                "channel_id": _channel_id(meta),
                "views": parse_count(rows[1][0]) if len(rows) > 1 and rows[1] else None,
            }
        )
    return [v for v in out if v["video_id"]]


def search_videos(data: dict) -> list[dict]:
    out = []
    for v in walk(data, "videoRenderer"):
        out.append(
            {
                "video_id": v.get("videoId"),
                "title": text(v.get("title")),
                "channel": text(v.get("ownerText")),
                "channel_id": _channel_id(v.get("ownerText", {})),
                "views": parse_count(text(v.get("viewCountText"))),
            }
        )
    return [v for v in out + lockups(data) if v["video_id"]]


def search_channels(data: dict) -> list[dict]:
    return [
        {"channel_id": c["channelId"], "channel": text(c.get("title")), "description": text(c.get("descriptionSnippet"))}
        for c in walk(data, "channelRenderer")
        if c.get("channelId")
    ]


def watch_info(data: dict) -> dict:
    """Watch page (/next): title (translated per hl), description, channel, and the Up next list."""
    pri = next(walk(data, "videoPrimaryInfoRenderer"), {})
    owner = next(walk(data, "videoOwnerRenderer"), {})
    return {
        "title": text(pri.get("title")),
        "description": next(walk(data, "attributedDescription"), {}).get("content", ""),
        "channel": text(owner.get("title")),
        "channel_id": _channel_id(owner),
        "related": lockups(data),
    }


# ---- Client ----

class Innertube:
    """Use as `async with Innertube(conn, hl, gl) as it:`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        hl: str = "en",
        gl: str = "US",
        transport: httpx.AsyncBaseTransport | None = None,
        fresh: bool = False,
    ):
        self.conn = conn
        self.hl, self.gl = hl, gl
        self.fresh = fresh
        self.calls = 0
        self.cache_hits = 0
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._http = httpx.AsyncClient(timeout=30, transport=transport, headers={"User-Agent": USER_AGENT})

    async def __aenter__(self) -> "Innertube":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _post(self, endpoint: str, body: dict, hl: str | None = None, gl: str | None = None) -> dict:
        hl, gl = hl or self.hl, gl or self.gl
        key = cache.make_key(f"innertube_{endpoint.replace('/', '_')}", {**body, "hl": hl, "gl": gl})
        if not self.fresh and (hit := cache.get(self.conn, key)) is not None:
            self.cache_hits += 1
            return hit
        payload = {"context": {"client": {"clientName": "WEB", "clientVersion": CLIENT_VERSION, "hl": hl, "gl": gl}}, **body}
        last: Exception | None = None
        async with self._sem:
            for attempt in range(3):
                try:
                    self.calls += 1
                    resp = await self._http.post(f"{URL}/{endpoint}", params={"prettyPrint": "false"}, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        cache.put(self.conn, key, data, TTL_INNERTUBE)
                        return data
                    last = InnertubeError(f"YouTube ({endpoint}) returned error {resp.status_code}")
                except (httpx.HTTPError, ValueError) as e:
                    last = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise InnertubeError(str(last))

    async def watch(self, video_id: str, hl: str | None = None, gl: str | None = None) -> dict:
        return watch_info(await self._post("next", {"videoId": video_id}, hl, gl))

    async def search(self, query: str, kind: str = "videos") -> list[dict]:
        params = SEARCH_VIDEOS if kind == "videos" else SEARCH_CHANNELS
        data = await self._post("search", {"query": query, "params": params})
        return search_videos(data) if kind == "videos" else search_channels(data)

    async def channel_videos(self, channel_id: str, hl: str | None = None, gl: str | None = None) -> list[dict]:
        """The channel's newest videos; titles auto-translated by YouTube per hl (works for en, not for every language)."""
        return lockups(await self._post("browse", {"browseId": channel_id, "params": CHANNEL_VIDEOS_TAB}, hl, gl))
