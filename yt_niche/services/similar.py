"""Video & kênh tương tự từ một URL video hoặc URL kênh (kiểu Related video / Related channel của TubeBuddy).

Luồng:
1. Đọc đầu vào. URL kênh → lấy 4 video nhiều view nhất trong 15 video gần nhất làm điểm xuất phát.
2. Thu tín hiệu (miễn phí, endpoint nội bộ của YouTube):
   - related: cột "Up next" của từng video xuất phát, ở thị trường đã chọn và thị trường của kênh gốc;
   - search / search_channel: search video và search kênh theo tiêu đề, tag và gợi ý autocomplete.
3. Lấy chi tiết kênh và video ứng viên bằng YouTube Data API (rẻ: channels/videos/playlistItems ≈ 1 unit).
4. Chấm điểm (core/similar) rồi lưu một dòng vào bảng similar_run.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx

from yt_niche import config as cfg
from yt_niche.core.outlier import channel_median_for, outlier_level
from yt_niche.core.similar import cooccur_raw, rank_channels, rank_videos
from yt_niche.services.fetch import _normalize_video
from yt_niche.sources.innertube import Innertube, InnertubeError
from yt_niche.sources.suggest import suggestions
from yt_niche.sources.youtube import YouTubeClient, YouTubeError

VIDEO, CHANNEL, HANDLE = "video", "channel", "handle"
SEARCH_VIDEO_TOP = 20
SEARCH_CHANNEL_TOP = 10


def _is_topic(name: str) -> bool:
    """Kênh nhạc YouTube tự tạo ("Tên ca sĩ - Topic"): không phải đối thủ nội dung."""
    return (name or "").endswith(" - Topic")


class SimilarError(ValueError):
    pass


def parse_input(url: str) -> tuple[str, str]:
    """URL video / Shorts / youtu.be, URL kênh /channel/UC… hoặc /@handle, hoặc id trần → (loại, id)."""
    s = unquote((url or "").strip())
    if m := re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})", s):
        return VIDEO, m.group(1)
    if m := re.search(r"(?:/channel/|^)(UC[\w-]{22})\b", s):
        return CHANNEL, m.group(1)
    if m := re.search(r"(?:youtube\.com/|^)(@[^/?#\s]+)", s):
        return HANDLE, m.group(1)
    if re.fullmatch(r"[\w-]{11}", s):
        return VIDEO, s
    raise SimilarError("Không đọc được URL. Dán link video (watch?v=…, youtu.be/…, /shorts/…) hoặc link kênh (/@tên, /channel/UC…).")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def channel_tags(keywords: str) -> list[str]:
    """brandingSettings.channel.keywords: 'animação "e se" ciência' → ['animação', 'e se', 'ciência']."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', keywords or "")]


def _channel_text(ch: dict, recent_titles: list[str], extra_titles: list[str]) -> str:
    return " | ".join(
        [ch.get("title", ""), ch.get("keywords", ""), (ch.get("description") or "")[:400]]
        + recent_titles[: cfg.SIMILAR_RECENT_TITLES]
        + extra_titles[:10]
    )


def _brand(item: dict) -> dict:
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    return {
        "channel_id": item["id"],
        "title": sn.get("title", ""),
        "handle": sn.get("customUrl", ""),
        "country": sn.get("country"),
        "description": sn.get("description", ""),
        "keywords": item.get("brandingSettings", {}).get("channel", {}).get("keywords", ""),
        "subs": None if st.get("hiddenSubscriberCount") else (int(st["subscriberCount"]) if "subscriberCount" in st else None),
        "video_count": int(st["videoCount"]) if "videoCount" in st else None,
        "uploads": item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
    }


async def _safe(coro, errors: list[str], default):
    try:
        return await coro
    except InnertubeError as e:
        errors.append(str(e))
        return default


async def collect(
    conn: sqlite3.Connection,
    url: str,
    region: str = "US",
    *,
    with_source_market: bool = True,
    fresh: bool = False,
    yt_transport: httpx.AsyncBaseTransport | None = None,
    it_transport: httpx.AsyncBaseTransport | None = None,
    api_key: str | None = None,
) -> dict:
    t0 = time.perf_counter()
    kind, ident = parse_input(url)
    hl = cfg.MARKETS.get(region, ("en",))[0]
    errors: list[str] = []

    async with YouTubeClient(conn, api_key=api_key, fresh=fresh, transport=yt_transport) as yt, Innertube(
        conn, hl, region, transport=it_transport, fresh=fresh
    ) as it:
        # ---- 1. Nguồn ----
        src_video = None
        if kind == VIDEO:
            items = await yt.videos([ident])
            if not items:
                raise SimilarError("Không tìm thấy video (đã xoá, riêng tư hoặc sai link).")
            src_video = items[0]
            cid = src_video["snippet"]["channelId"]
        elif kind == HANDLE:
            cid = await yt.channel_by_handle(ident)
            if not cid:
                raise SimilarError(f"Không tìm thấy kênh {ident}.")
        else:
            cid = ident
        src_items = await yt.channels_branding([cid])
        if not src_items:
            raise SimilarError("Không tìm thấy kênh.")
        src = _brand(src_items[0])
        recent = await yt.recent_uploads(src["uploads"], cfg.SIMILAR_RECENT_TITLES) if src["uploads"] else []

        if src_video:
            seeds = [src_video]
        else:
            recent_items = await yt.videos([r["video_id"] for r in recent]) if recent else []
            recent_items = [v for v in recent_items if v["snippet"].get("liveBroadcastContent", "none") == "none"]
            recent_items.sort(key=lambda v: -int(v.get("statistics", {}).get("viewCount", 0)))
            seeds = recent_items[: cfg.SIMILAR_SEED_VIDEOS]
            if not seeds:
                raise SimilarError("Kênh chưa có video công khai để làm điểm xuất phát.")

        locales = [(hl, region)]
        src_region = src["country"]
        if with_source_market and src_region in cfg.MARKETS and src_region != region:
            locales.append((cfg.MARKETS[src_region][0], src_region))

        # ---- 2. Tín hiệu ----
        cand_v: dict[str, dict] = {}
        sig_c: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        names: dict[str, str] = {}
        local_titles: dict[str, list[str]] = defaultdict(list)  # tiêu đề (đã dịch theo hl) của video từng kênh

        def add_v(v: dict, sig: str, w: float) -> None:
            if not v.get("video_id") or not v.get("channel_id") or _is_topic(v.get("channel", "")):
                return
            r = cand_v.setdefault(v["video_id"], {**v, "signals": defaultdict(float)})
            r["signals"][sig] += w
            sig_c[v["channel_id"]][sig] += w
            names.setdefault(v["channel_id"], v.get("channel", ""))
            if v["title"] not in local_titles[v["channel_id"]]:
                local_titles[v["channel_id"]].append(v["title"])

        pairs = [(s["id"], h, g) for s in seeds for h, g in locales]
        watches, src_local = await asyncio.gather(
            asyncio.gather(*(_safe(it.watch(vid, h, g), errors, None) for vid, h, g in pairs)),
            asyncio.gather(*(_safe(it.channel_videos(cid, h, g), errors, []) for h, g in locales)),
        )
        # Tiêu đề video xuất phát / video của kênh gốc theo từng hl (YouTube tự dịch), để so được khác ngôn ngữ.
        seed_local: dict[str, list[str]] = defaultdict(list)
        for (_, h, _g), w in zip(pairs, watches):
            if not w:
                continue
            if w["title"]:
                seed_local[h].append(w["title"])
            for v in w["related"]:
                add_v(v, "related", 1.0)
        chan_local = {h: [v["title"] for v in vids] for (h, _g), vids in zip(locales, src_local)}
        seed_local_titles = list(dict.fromkeys(t for ts in seed_local.values() for t in ts))

        seed_titles = [s["snippet"].get("title", "") for s in seeds]
        seed_tags = [t for s in seeds for t in s["snippet"].get("tags", [])]
        tags = channel_tags(src["keywords"])
        queries = seed_local_titles[:2] + seed_titles + [t for t in tags if len(t) > 3][:4] + seed_tags[:4]
        auto = (
            await suggestions(conn, seed_titles[0].lower()[:30], region, hl, transport=it_transport) if seed_titles else []
        )
        queries += [a["keyword"] for a in auto[:3]]
        queries = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))[: cfg.SIMILAR_MAX_QUERIES]

        results = await asyncio.gather(
            *(_safe(it.search(q, what), errors, []) for q in queries for what in ("videos", "channels"))
        )
        for i, res in enumerate(results):
            if i % 2 == 0:
                for rank, v in enumerate(res[:SEARCH_VIDEO_TOP]):
                    add_v(v, "search", 1.0 / (1 + rank / 5))
            else:
                for rank, c in enumerate(res[:SEARCH_CHANNEL_TOP]):
                    if _is_topic(c["channel"]):
                        continue
                    sig_c[c["channel_id"]]["search_channel"] += 1.0 / (1 + rank / 3)
                    names.setdefault(c["channel_id"], c["channel"])

        sig_c.pop(cid, None)

        # ---- 3. Chi tiết qua YouTube Data API ----
        top_c = sorted(sig_c, key=lambda c: cooccur_raw(sig_c[c]), reverse=True)[: cfg.SIMILAR_ENRICH_CHANNELS]
        ch_items = await yt.channels_branding(top_c) if top_c else []
        medians = await yt.channel_medians(ch_items) if ch_items else {}
        briefs = {item["id"]: _brand(item) for item in ch_items}
        recents = await asyncio.gather(
            *(yt.recent_uploads(b["uploads"], cfg.SIMILAR_RECENT_TITLES) if b["uploads"] else asyncio.sleep(0, [])
              for b in briefs.values())
        )
        recent_by_c = dict(zip(briefs, recents))

        top_v = sorted(
            (v for v in cand_v.values() if v["channel_id"] != cid),
            key=lambda v: -sum(v["signals"].values()),
        )[: cfg.SIMILAR_ENRICH_VIDEOS]
        v_items = {v["id"]: v for v in await yt.videos([v["video_id"] for v in top_v])} if top_v else {}

    # ---- 4. Chấm điểm ----
    channel_cands = []
    for c, b in briefs.items():
        if _is_topic(b["title"]):
            continue
        titles = [r["title"] for r in recent_by_c.get(c, [])]
        row = medians.get(c, {})
        channel_cands.append(
            {
                "channel_id": c,
                "channel": b["title"] or names.get(c, ""),
                "handle": b["handle"],
                "country": b["country"],
                "subs": b["subs"],
                "video_count": b["video_count"],
                "median_long": row.get("median_long"),
                "median_short": row.get("median_short"),
                "signals": {k: round(v, 2) for k, v in sig_c[c].items()},
                "sample": titles[:3],
                "text": _channel_text(b, titles, local_titles.get(c, [])),
            }
        )
    src_channel_texts = [_channel_text(src, [r["title"] for r in recent], [])] + [
        _channel_text({"title": src["title"]}, chan_local.get(h, []), seed_local.get(h, [])) for h, _g in locales
    ]
    channels = rank_channels(src_channel_texts, channel_cands)

    video_cands = []
    for v in top_v:
        item = v_items.get(v["video_id"])
        norm = _normalize_video(item) if item else None
        if not norm:
            continue  # đã xoá / riêng tư / đang livestream
        brief = briefs.get(norm["channel_id"]) or {}
        med = channel_median_for(medians.get(norm["channel_id"]), norm["fmt"])
        ratio = norm["views"] / med if norm["views"] is not None and med else None
        video_tags = item["snippet"].get("tags", [])
        video_cands.append(
            {
                **norm,
                "title_local": v["title"],
                "channel": brief.get("title") or v.get("channel", ""),
                "subs": brief.get("subs"),
                "outlier": round(ratio, 2) if ratio else None,
                "level": outlier_level(ratio),
                "signals": {k: round(x, 2) for k, x in v["signals"].items()},
                "text": " | ".join([v["title"], norm["title"]] + video_tags[:10]),
            }
        )
    src_video_texts = [
        " | ".join(seed_titles + seed_tags[:15] + [(seeds[0]["snippet"].get("description") or "")[:300]])
    ] + [" | ".join(seed_local.get(h, [])) for h, _g in locales]
    videos = rank_videos(src_video_texts, video_cands)

    return {
        "input": url,
        "kind": VIDEO if kind == VIDEO else CHANNEL,
        "region": region,
        "locales": [g for _, g in locales],
        "source": {
            "channel_id": cid,
            "channel": src["title"],
            "handle": src["handle"],
            "subs": src["subs"],
            "country": src["country"],
            "keywords": tags,
            "video_id": src_video["id"] if src_video else None,
            "video_title": src_video["snippet"].get("title") if src_video else None,
            "video_views": int(src_video["statistics"]["viewCount"]) if src_video and "viewCount" in src_video.get("statistics", {}) else None,
        },
        "seeds": [{"video_id": s["id"], "title": s["snippet"].get("title", "")} for s in seeds],
        "queries": queries,
        "channels": channels,
        "videos": videos,
        "stats": {
            "units": yt.meter.total,
            "innertube_calls": it.calls,
            "innertube_cache_hits": it.cache_hits,
            "candidates": {"channels": len(sig_c), "videos": len(cand_v)},
            "errors": errors[:10],
            "elapsed_s": round(time.perf_counter() - t0, 1),
        },
    }


# ---- Lưu và đọc ----

def find_similar(conn: sqlite3.Connection, url: str, region: str = "US", **kw) -> int:
    """Chạy một lần tra và lưu lại; trả về id dòng similar_run. Lỗi: SimilarError, YouTubeError, QuotaExceeded."""
    if not cfg.api_key() and not kw.get("api_key"):
        raise YouTubeError("Chưa có YouTube API key. Vào trang Cài đặt để nhập.")
    result = asyncio.run(collect(conn, url, region, **kw))
    src = result["source"]
    label = src["video_title"] or src["channel"]
    return conn.execute(
        """INSERT INTO similar_run (created_at, input, kind, region, label, n_channels, n_videos, result_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (_now(), url, result["kind"], region, label, len(result["channels"]), len(result["videos"]),
         json.dumps(result, ensure_ascii=False)),
    ).lastrowid


def list_runs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, input, kind, region, label, n_channels, n_videos FROM similar_run ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM similar_run WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["result"] = json.loads(d.pop("result_json"))
    return d


def delete_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM similar_run WHERE id = ?", (run_id,))
