"""Gợi ý tìm kiếm YouTube (autocomplete): miễn phí, không tốn quota API. Có cache SQLite 24h."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx

from yt_niche.config import TTL_SUGGEST
from yt_niche.db import cache

URL = "https://suggestqueries.google.com/complete/search"
CONCURRENCY = 4
# Mở rộng A–Z: thêm một chữ cái sau từ gốc để lấy biến thể.
AZ_LETTERS = "abcdeghiklmnopqrstuvxy"  # bảng chữ cái tiếng Việt không có f, j, w, z
AZ_LETTERS_LATIN = "abcdefghijklmnopqrstuvwxyz"


class SuggestError(RuntimeError):
    pass


def _parse(text: str) -> list[str]:
    data = json.loads(text)
    return [s for s in data[1] if isinstance(s, str)]


async def _one(http: httpx.AsyncClient, conn: sqlite3.Connection, query: str, region: str, language: str) -> list[str]:
    params = {"client": "firefox", "ds": "yt", "q": query, "hl": language, "gl": region}
    key = cache.make_key("suggest", params)
    if (hit := cache.get(conn, key)) is not None:
        return hit
    resp = await http.get(URL, params=params)
    if resp.status_code != 200:
        raise SuggestError(f"Gợi ý YouTube lỗi {resp.status_code}")
    items = _parse(resp.text)
    cache.put(conn, key, items, TTL_SUGGEST)
    return items


async def suggestions(
    conn: sqlite3.Connection,
    seed: str,
    region: str,
    language: str,
    *,
    az: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict]:
    """[{"keyword", "source": suggest | az, "rank"}], bỏ trùng, giữ thứ tự (gợi ý gốc trước)."""
    seed = seed.strip()
    queries = [(seed, "suggest")]
    if az:
        letters = AZ_LETTERS if language == "vi" else AZ_LETTERS_LATIN
        queries += [(f"{seed} {ch}", "az") for ch in letters]

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=15, transport=transport) as http:

        async def run(q: str) -> list[str]:
            async with sem:
                try:
                    return await _one(http, conn, q, region, language)
                except (httpx.HTTPError, SuggestError, ValueError):
                    return []  # một chữ cái lỗi không làm hỏng cả danh sách

        results = await asyncio.gather(*(run(q) for q, _ in queries))

    out: dict[str, dict] = {}
    for (_, source), items in zip(queries, results):
        for rank, kw in enumerate(items, 1):
            kw = " ".join(kw.lower().split())
            if kw and kw not in out:
                out[kw] = {"keyword": kw, "source": source, "rank": rank}
    return list(out.values())
