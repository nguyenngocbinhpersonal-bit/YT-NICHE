"""Fetch question-type comments on the outperformer videos of a dig (≈ 1 unit / video, cached 24h)."""

from __future__ import annotations

import asyncio
import sqlite3

from yt_niche.core.questions import question_comments
from yt_niche.sources.youtube import YouTubeClient

MAX_VIDEOS = 10


async def _fetch(conn: sqlite3.Connection, video_ids: list[str]) -> tuple[list[dict], int]:
    async with YouTubeClient(conn) as yt:
        pages = await asyncio.gather(*(yt.comment_threads(v) for v in video_ids))
    return [c for page in pages for c in page], yt.meter.total


def questions_for_run(conn: sqlite3.Connection, run: dict, n_videos: int = MAX_VIDEOS) -> dict:
    outliers = run["result"]["outliers"][:n_videos]
    titles = {v["video_id"]: v["title"] for v in outliers}
    comments, units = asyncio.run(_fetch(conn, list(titles)))
    questions = [{**q, "video_title": titles.get(q["video_id"], "")} for q in question_comments(comments)]
    return {"questions": questions, "n_comments": len(comments), "n_videos": len(titles), "units": units}
