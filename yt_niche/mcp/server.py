"""MCP server that lets Claude Code drive the app (docs/NICHE_MAP_MCP_PLAN.md).

Run: `python -m yt_niche.mcp` (stdio). Claude Code starts it automatically through .mcp.json at the repo root.

Three groups of tools:
- read (no quota): quota_status, map_list, map_compare, niche_get, runs_list, run_get, radar_keywords, niche_events
- fetch real data (spends YouTube quota; the tool reports the units spent): keywords_suggest, keywords_score, niche_dig,
  similar_from_url, competitors_refresh
- write decisions (rationale required): niche_create, niche_update, niche_attach_keywords, niche_detach_keywords,
  niche_attach_run, niche_set_channels, niche_move, niche_merge, niche_unmerge, niche_set_motivation, niche_rank, niche_drop

The server only calls services/ and holds no formulas. Every business error is returned as a string "Error: ..." so Claude
can read it and correct itself.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from mcp.server.mcpserver import MCPServer

from yt_niche import config as cfg
from yt_niche.db import quota
from yt_niche.db import runs as run_repo
from yt_niche.db.connection import connect
from yt_niche.services import niche_map as nm

INSTRUCTIONS = """YT-Niche: a local YouTube niche-picking tool. You (Claude) do the judgement work: infer keywords,
name niches, build the tree, assign audience motivations, merge nearby niches, rank potential. The app does the mechanical
work: fetch real numbers, score, store, draw. Rules: (1) call quota_status before anything that spends quota; (2) only use
kind=main for keywords that have real numbers (keywords_score or radar); (3) rank potential with niche_rank after reading
map_compare, never before; (4) every decision carries a 1–2 sentence rationale. Read the `yt-niche` skill carefully if present."""

server = MCPServer("yt-niche", instructions=INSTRUCTIONS)
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


def _guard(units_needed: int, force: bool) -> str | None:
    """Block quota-spending work when it would eat into the share reserved for Streamlit (cfg.MCP_QUOTA_RESERVE)."""
    used = quota.used_on(_db())
    left = cfg.YOUTUBE_DAILY_QUOTA - used
    if not force and left - units_needed < cfg.MCP_QUOTA_RESERVE:
        return (
            f"Error: this needs ≈{units_needed} units, {left} are left and {cfg.MCP_QUOTA_RESERVE} must stay reserved for the UI. "
            "Reduce the number of keywords / pages, wait until tomorrow, or call again with force=true if the user agrees."
        )
    return None


def _err(e: Exception) -> str:
    return f"Error: {e}"


def _run_brief(run: dict) -> dict:
    """Summary of a dig with enough for Claude to judge, without the raw video list."""
    res = run.get("result") or {}
    booms = res.get("small_boom_channels") or []
    return {
        "id": run["id"], "topic": run["topic"], "region": run["region"], "language": run.get("language"), "window_days": run["window_days"], "fmt": run["fmt"],
        "created_at": run["created_at"], "parent_id": run.get("parent_id"), "layer": run.get("layer"),
        "score": run["score"], "label": run["label"], "confidence": run["confidence"], "verdict": run["verdict"],
        "bars": {k: res.get(k) for k in ("demand", "opportunity", "freshness", "competition")},
        "n": res.get("n"), "median_views": res.get("median_views"),
        "outlier_counts": res.get("outlier_counts"),
        "small_boom_channels": [
            {k: c.get(k) for k in ("channel_id", "title", "subs", "age_months", "best_outlier", "video_count")} for c in booms[:8]
        ],
        "trends": (res.get("trends") or {}).get("class"),
        "three_tier": res.get("three_tier"),
        "income_per_video": res.get("income_per_video"),
        "note": res.get("note"),
        "top_outliers": [
            {k: v.get(k) for k in ("title", "channel_id", "views", "outlier", "published_at")} for v in (res.get("outliers") or [])[:10]
        ],
    }


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@server.tool(description="YouTube quota used / left today, Google Trends cooldown state, and the share reserved for the UI.")
def quota_status() -> dict:
    from yt_niche.sources import trends

    conn = _db()
    used = quota.used_on(conn)
    until = trends.cooldown_until(conn)
    return {
        "youtube_units_used": used,
        "youtube_units_left": cfg.YOUTUBE_DAILY_QUOTA - used,
        "youtube_daily_quota": cfg.YOUTUBE_DAILY_QUOTA,
        "reserve_for_ui": cfg.MCP_QUOTA_RESERVE,
        "spendable": max(0, cfg.YOUTUBE_DAILY_QUOTA - used - cfg.MCP_QUOTA_RESERVE),
        "trends_cooldown_until": until.isoformat() if until else None,
        "unit_costs": {"keyword_score": 102, "dig": "210–350", "similar_from_url": "10–30", "suggest": 0},
    }


@server.tool(description="The whole niche tree (flat, with depth and path), potential, and the number of keywords / digs / channels per niche.")
def map_list(include_merged: bool = False, include_dropped: bool = False) -> list[dict]:
    return nm.list_tree(_db(), include_merged=include_merged, include_dropped=include_dropped)


@server.tool(description="Comparison table of the niches' real numbers (demand, competition, dig score, trend, small booming channels) with the current rank and the stale flag. Read it before niche_rank.")
def map_compare(niche_ids: list[int] | None = None) -> list[dict]:
    return nm.compare_table(_db(), niche_ids)


@server.tool(description="One niche in detail: keywords by kind with numbers, digs, channels, aggregated signals, the 10 latest events.")
def niche_get(niche_id: int) -> dict | str:
    try:
        return nm.get(_db(), niche_id)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Decision log (who, what, why). Leave niche_id empty for the whole map.")
def niche_events(niche_id: int | None = None, limit: int = 50) -> list[dict]:
    return nm.events(_db(), niche_id, limit)


@server.tool(description="Which niches already hold these keywords (to decide between attaching to an existing niche, creating a new one or merging).")
def niche_find_by_keywords(keywords: list[dict]) -> list[dict]:
    """keywords: [{keyword, region}]"""
    return nm.find_similar_niches(_db(), keywords)


@server.tool(description="List of existing digs (summary). Filter by topic substring and region.")
def runs_list(contains: str | None = None, region: str | None = None, limit: int = 50) -> list[dict]:
    out = []
    for r in run_repo.list_all(_db()):
        if contains and contains.lower() not in r["topic"].lower():
            continue
        if region and r["region"] != region.upper():
            continue
        out.append({k: r[k] for k in ("id", "topic", "region", "language", "window_days", "fmt", "created_at", "score", "label", "confidence", "verdict", "parent_id", "layer")})
        if len(out) >= limit:
            break
    return out


@server.tool(description="One dig: score, four bars, three tiers, small booming channels, outperformer videos, Trends.")
def run_get(run_id: int) -> dict | str:
    run = run_repo.get(_db(), run_id)
    return _run_brief(run) if run else f"Error: no dig with id={run_id}"


@server.tool(description="Radar keywords (Google Trends category scan) by status: pending | passed | watching | excluded | ignored.")
def radar_keywords(statuses: list[str] | None = None, regions: list[str] | None = None, limit: int = 100) -> list[dict]:
    from yt_niche.services import radar

    rows = radar.keywords(_db(), statuses or ["passed", "watching"], regions)
    keep = ("region", "keyword", "kind", "value", "status", "reason", "trend_class", "yt_score", "potential", "ai_type", "ai_vi", "seen_days", "last_seen")
    return [{k: r.get(k) for k in keep} for r in rows[:limit]]


@server.tool(description="Keywords already suggested / scored in the keyword table, by seed and market. No quota.")
def keywords_list(seed: str | None = None, region: str | None = None, scored_only: bool = False, limit: int = 100) -> list[dict]:
    from yt_niche.services import keywords

    rows = keywords.list_keywords(_db(), seed, region.upper() if region else None)
    keep = ("region", "keyword", "seed", "source", "rank", "score", "demand", "competition", "n", "low_data", "scored_at", "run_id")
    out = [{k: r.get(k) for k in keep} for r in rows if not scored_only or r["score"] is not None]
    return out[:limit]


# ---------------------------------------------------------------------------
# Fetch real data
# ---------------------------------------------------------------------------


@server.tool(description="YouTube search suggestions for a seed (0 units). az=true also fetches the a–z variants. Returns the keywords with their score if scored before.")
def keywords_suggest(seed: str, region: str = "VN", az: bool = False) -> dict | str:
    from yt_niche.services import keywords

    conn = _db()
    try:
        items = keywords.fetch_suggestions(conn, seed, region.upper(), az=az)
    except Exception as e:  # noqa: BLE001 — Claude needs to read network / input errors
        return _err(e)
    rows = keywords.list_keywords(conn, seed, region.upper())
    return {
        "seed": seed, "region": region.upper(), "n_new": len(items),
        "keywords": [{k: r.get(k) for k in ("keyword", "source", "rank", "score", "demand", "competition", "n", "scored_at")} for r in rows],
    }


@server.tool(description="Score keywords with real YouTube search results (≈102 units / keyword, at most 10). The keywords must be in the keyword table (keywords_suggest first, or add_manual=true).")
def keywords_score(region: str, keywords_to_score: list[str], seed: str | None = None, add_manual: bool = False, force: bool = False) -> dict | str:
    from yt_niche.services import keywords

    conn = _db()
    region = region.upper()
    if not keywords_to_score:
        return "Error: the keyword list is empty"
    if msg := _guard(keywords.UNITS_PER_KEYWORD * len(keywords_to_score), force):
        return msg
    try:
        if add_manual:
            keywords.add_manual(conn, seed or keywords_to_score[0], region, keywords_to_score)
        units = keywords.score(conn, region, [k.lower().strip() for k in keywords_to_score])
    except Exception as e:  # noqa: BLE001
        return _err(e)
    rows = {r["keyword"]: r for r in keywords.list_keywords(conn, None, region)}
    return {
        "units_spent": units,
        "scored": [
            {k: rows[kw].get(k) for k in ("keyword", "score", "demand", "competition", "n", "low_data", "metrics")}
            for kw in (x.lower().strip() for x in keywords_to_score) if kw in rows
        ],
    }


@server.tool(description="Dig a niche: YouTube search + videos + channels + Trends → niche score, three tiers, small booming channels (≈210–350 units). If a dig with the same setup exists from the last 90 days, that dig is returned instead of digging again, unless fresh=true.")
def niche_dig(
    topic: str, region: str = "VN", window_days: int = 30, fmt: str = "any", pages: int = 2,
    with_trends: bool = True, parent_run_id: int | None = None, fresh: bool = False, force: bool = False,
) -> dict | str:
    from datetime import datetime, timedelta, timezone

    from yt_niche.services import runs as run_service
    from yt_niche.services.fetch import DigParams

    conn = _db()
    region = region.upper()
    if region not in cfg.MARKETS:
        return f"Error: market {region} is not in the list {list(cfg.ENABLED_MARKETS)}"
    if fmt not in ("any", "long", "shorts"):
        return "Error: fmt must be any | long | shorts"
    topic_n = " ".join(topic.lower().split())
    if not fresh:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        for r in run_repo.list_all(conn):
            if (r["topic"].lower() == topic_n and r["region"] == region and r["window_days"] == window_days
                    and r["fmt"] == fmt and r["created_at"] >= cutoff):
                return {"reused": True, **_run_brief(run_repo.get(conn, r["id"]))}
    if msg := _guard(110 * pages + 60, force):
        return msg
    before = quota.used_on(conn)
    try:
        run_id = run_service.dig(
            conn,
            DigParams(topic=topic, region=region, language=cfg.MARKETS[region][0], window_days=window_days, fmt=fmt,
                      pages=pages, with_trends=with_trends),
            category=None, fresh=fresh, parent_id=parent_run_id,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return {"reused": False, "units_spent": quota.used_on(conn) - before, **_run_brief(run_repo.get(conn, run_id))}


@server.tool(description="From a YouTube video / channel URL: similar channels and videos (competitors), winning video titles, the source channel's tags (≈10–30 units). region sets the search language: it must belong to the language of the niche in question. Each channel comes with its country; drop channels in another language, never use them as competitors or keyword sources.")
def similar_from_url(url: str, region: str = "US", force: bool = False) -> dict | str:
    from yt_niche.services import similar

    conn = _db()
    try:
        language = nm.language_of_region(region)
    except nm.NicheError as e:
        return _err(e)
    if msg := _guard(30, force):
        return msg
    try:
        sid = similar.find_similar(conn, url, region.upper())
    except Exception as e:  # noqa: BLE001
        return _err(e)
    res = similar.get_run(conn, sid)["result"]
    return {
        "similar_run_id": sid, "region": region.upper(), "language": language,
        "regions_same_language": nm.regions_of_language(language),
        "kind": res.get("kind"), "source": res.get("source"),
        "seed_titles": [s["title"] for s in res.get("seeds", [])],
        "queries": res.get("queries", [])[:20],
        "channels": [
            {k: c.get(k) for k in ("channel_id", "title", "subs", "score", "content", "cooccur", "country", "keywords")}
            for c in res.get("channels", [])[:15]
        ],
        "videos": [{k: v.get(k) for k in ("video_id", "title", "channel_id", "views", "score")} for v in res.get("videos", [])[:20]],
        "units_spent": (res.get("stats") or {}).get("units"),
    }


@server.tool(description="Rescan the channels attached to a niche and the small booming channels of its digs: subs, current median views (≈2 units / channel). Returns a table for Claude to decide leader / rising / fallen.")
def competitors_refresh(niche_id: int, force: bool = False) -> dict | str:
    import asyncio

    from yt_niche.sources.youtube import YouTubeClient

    conn = _db()
    try:
        d = nm.get(conn, niche_id)
    except nm.NicheError as e:
        return _err(e)
    ids = {c["channel_id"] for c in d["channels"]}
    for r in d["runs"]:
        run = run_repo.get(conn, r["id"])
        for c in (run.get("result") or {}).get("small_boom_channels") or []:
            if c.get("channel_id"):
                ids.add(c["channel_id"])
    if not ids:
        return {"channels": [], "note": "The niche has no channels yet; attach some with niche_set_channels or dig first."}
    if msg := _guard(2 * len(ids), force):
        return msg

    async def go() -> list[dict]:
        async with YouTubeClient(conn, fresh=True) as yt:
            items = await yt.channels_branding(sorted(ids))
        return items

    try:
        items = asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        return _err(e)
    known = {c["channel_id"]: c for c in d["channels"]}
    allowed = set(d["regions_allowed"])
    out = []
    for it in items:
        sn, st = it.get("snippet", {}), it.get("statistics", {})
        country = sn.get("country")
        lang = sn.get("defaultLanguage") or it.get("brandingSettings", {}).get("channel", {}).get("defaultLanguage")
        base_lang = (lang or "").split("-")[0].lower() or None
        mismatch = (country is not None and country not in allowed) or (base_lang is not None and base_lang != d["language"].split("-")[0].lower())
        out.append({
            "channel_id": it["id"], "title": sn.get("title"),
            "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
            "video_count": int(st.get("videoCount", 0)), "views_total": int(st.get("viewCount", 0)),
            "created_at": sn.get("publishedAt"), "country": country, "default_language": lang,
            "language_mismatch": mismatch,
            "current_role": known.get(it["id"], {}).get("role"),
        })
    return {
        "niche_language": d["language"], "regions_allowed": sorted(allowed), "channels": out, "units_spent": 2 * len(ids),
        "note": "language_mismatch=true: the channel declares a country / language different from the niche — consider dropping it from the competitors.",
    }


# ---------------------------------------------------------------------------
# Write decisions
# ---------------------------------------------------------------------------


@server.tool(description=f"Create a niche. One niche = one language: pass language (vi, en, ja…) or region (VN, US…) to infer it. Keywords, digs, parent and merged niches must later share that language. motivation ∈ {list(nm.MOTIVATIONS)}. A new niche stays draft until it has a scored main keyword or a dig.")
def niche_create(
    name: str, rationale: str, language: str | None = None, region: str | None = None, parent_id: int | None = None,
    motivation: str | None = None, motivation_secondary: str | None = None, summary: str = "",
) -> dict | str:
    try:
        if not language:
            if not region:
                return "Error: language (vi, en…) or region (VN, US…) is needed to know which language the niche belongs to"
            language = nm.language_of_region(region)
        nid = nm.create(_db(), name, language=language, parent_id=parent_id, motivation=motivation,
                        motivation_secondary=motivation_secondary, summary=summary, rationale=rationale)
        return nm.get(_db(), nid)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Rename a niche / change its summary.")
def niche_update(niche_id: int, rationale: str, name: str | None = None, summary: str | None = None) -> dict | str:
    try:
        return nm.update(_db(), niche_id, name=name, summary=summary, rationale=rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Attach keywords to a niche. items: [{keyword, region, kind: main|related|longtail|trend, source?}]. region must share the niche's language (see regions_allowed in niche_get). Numbers are copied from the keyword / radar tables at attach time; kind=main should only be used for keywords that have numbers.")
def niche_attach_keywords(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.attach_keywords(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Remove keywords from a niche. items: [{keyword, region}].")
def niche_detach_keywords(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.detach_keywords(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Attach a dig (dig_run) to a niche; the dig must share the niche's language.")
def niche_attach_run(niche_id: int, run_id: int, rationale: str = "") -> dict | str:
    try:
        return nm.attach_run(_db(), niche_id, run_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Record the niche's competitor channels. items: [{channel_id, role: leader|rising|fallen, note?}].")
def niche_set_channels(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.set_channels(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Move a niche under another parent (new_parent_id empty = to the root).")
def niche_move(niche_id: int, rationale: str, new_parent_id: int | None = None) -> dict | str:
    try:
        return nm.move(_db(), niche_id, new_parent_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Soft-merge source into target (same language required): keywords, digs, channels and children move to the target; the source becomes merged and can be split again with niche_unmerge.")
def niche_merge(source_id: int, target_id: int, rationale: str) -> dict | str:
    try:
        return nm.merge(_db(), source_id, target_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Undo a merge.")
def niche_unmerge(niche_id: int, rationale: str) -> dict | str:
    try:
        return nm.unmerge(_db(), niche_id, rationale, actor="claude")
    except nm.NicheError as e:
        return _err(e)


@server.tool(description=f"Assign the audience motivation (primary + optional secondary). Codes: {nm.MOTIVATIONS}")
def niche_set_motivation(niche_id: int, primary: str, rationale: str, secondary: str | None = None) -> dict | str:
    try:
        return nm.set_motivation(_db(), niche_id, primary, secondary, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Rank the potential 0–100 of one or more niches after reading map_compare. items: [{niche_id, potential, rationale}]. The app stores a snapshot of the numbers to flag when the rank goes stale.")
def niche_rank(items: list[dict], rationale: str = "") -> list[dict] | str:
    try:
        return nm.rank(_db(), items, rationale)
    except (nm.NicheError, KeyError, ValueError) as e:
        return _err(e)


@server.tool(description="Drop a niche (status=dropped, no data deleted).")
def niche_drop(niche_id: int, rationale: str) -> dict | str:
    try:
        return nm.drop(_db(), niche_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Copy the latest numbers from the keyword / radar tables for every keyword attached to the niche (after re-scoring).")
def niche_refresh_numbers(niche_id: int) -> dict | str:
    try:
        n = nm.refresh_keyword_numbers(_db(), niche_id)
        return {"niche_id": niche_id, "updated_keywords": n, "signals": nm.signals(_db(), niche_id)}
    except nm.NicheError as e:
        return _err(e)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
