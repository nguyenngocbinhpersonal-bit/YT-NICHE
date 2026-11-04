"""MCP server để Claude Code điều khiển app (docs/NICHE_MAP_MCP_PLAN.md).

Chạy: `python -m yt_niche.mcp` (stdio). Claude Code tự bật qua .mcp.json ở gốc repo.

Ba nhóm tool:
- đọc (không tốn quota): quota_status, map_list, map_compare, niche_get, runs_list, run_get, radar_keywords, niche_events
- lấy dữ liệu thật (tốn quota YouTube, tool trả luôn số unit đã tiêu): keywords_suggest, keywords_score, niche_dig,
  similar_from_url, competitors_refresh
- ghi quyết định (bắt buộc có rationale): niche_create, niche_update, niche_attach_keywords, niche_detach_keywords,
  niche_attach_run, niche_set_channels, niche_move, niche_merge, niche_unmerge, niche_set_motivation, niche_rank, niche_drop

Server chỉ gọi services/, không chứa công thức. Mọi lỗi nghiệp vụ trả về chuỗi "Lỗi: ..." để Claude đọc được và tự sửa.
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

INSTRUCTIONS = """YT-Niche: công cụ chọn ngách YouTube chạy local. Bạn (Claude) làm việc phán đoán: suy ra từ khoá,
đặt tên ngách, dựng cây, gán động cơ khán giả, gộp ngách gần nhau, xếp tiềm năng. App làm việc cơ học: lấy số thật,
tính điểm, lưu, vẽ. Quy tắc: (1) gọi quota_status trước việc tốn quota; (2) chỉ gắn kind=main cho từ khoá đã có số
thật (keywords_score hoặc radar); (3) xếp tiềm năng bằng niche_rank sau khi đọc map_compare, không trước;
(4) mỗi quyết định ghi kèm rationale 1–2 câu. Đọc kỹ skill `yt-niche` nếu có."""

server = MCPServer("yt-niche", instructions=INSTRUCTIONS)
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = connect()
    return _conn


def _guard(units_needed: int, force: bool) -> str | None:
    """Chặn việc tốn quota nếu sẽ ăn vào phần dành cho Streamlit (cfg.MCP_QUOTA_RESERVE)."""
    used = quota.used_on(_db())
    left = cfg.YOUTUBE_DAILY_QUOTA - used
    if not force and left - units_needed < cfg.MCP_QUOTA_RESERVE:
        return (
            f"Lỗi: việc này cần ≈{units_needed} unit, quota còn {left} và phải giữ {cfg.MCP_QUOTA_RESERVE} cho giao diện. "
            "Giảm số từ khoá / số trang, đợi ngày mai, hoặc gọi lại với force=true nếu người dùng đồng ý."
        )
    return None


def _err(e: Exception) -> str:
    return f"Lỗi: {e}"


def _run_brief(run: dict) -> dict:
    """Tóm tắt một lần đào đủ để Claude phán đoán, không kèm danh sách video thô."""
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
# Đọc
# ---------------------------------------------------------------------------


@server.tool(description="Quota YouTube đã dùng / còn lại hôm nay, trạng thái tạm chặn Google Trends, ngưỡng giữ cho giao diện.")
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


@server.tool(description="Toàn bộ cây ngách (phẳng, có depth và path), tiềm năng, số từ khoá / lần đào / kênh của mỗi ngách.")
def map_list(include_merged: bool = False, include_dropped: bool = False) -> list[dict]:
    return nm.list_tree(_db(), include_merged=include_merged, include_dropped=include_dropped)


@server.tool(description="Bảng so sánh số thật của các ngách (nhu cầu, cạnh tranh, điểm đào, trend, kênh nhỏ nổ) kèm hạng hiện tại và cờ stale. Đọc trước khi niche_rank.")
def map_compare(niche_ids: list[int] | None = None) -> list[dict]:
    return nm.compare_table(_db(), niche_ids)


@server.tool(description="Chi tiết một ngách: từ khoá theo loại kèm số, lần đào, kênh, số liệu tổng hợp, 10 sự kiện gần nhất.")
def niche_get(niche_id: int) -> dict | str:
    try:
        return nm.get(_db(), niche_id)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Nhật ký quyết định (ai, làm gì, vì sao). niche_id bỏ trống = toàn bản đồ.")
def niche_events(niche_id: int | None = None, limit: int = 50) -> list[dict]:
    return nm.events(_db(), niche_id, limit)


@server.tool(description="Ngách nào đã chứa các từ khoá này (để quyết định gắn vào ngách cũ, tạo mới hay gộp).")
def niche_find_by_keywords(keywords: list[dict]) -> list[dict]:
    """keywords: [{keyword, region}]"""
    return nm.find_similar_niches(_db(), keywords)


@server.tool(description="Danh sách các lần đào đã có (tóm tắt). Lọc theo topic chứa chuỗi, region.")
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


@server.tool(description="Một lần đào: điểm, 4 thanh, 3 tầng, kênh nhỏ nổ, video vượt trội, Trends.")
def run_get(run_id: int) -> dict | str:
    run = run_repo.get(_db(), run_id)
    return _run_brief(run) if run else f"Lỗi: không có lần đào id={run_id}"


@server.tool(description="Từ khoá Radar (Google Trends quét danh mục) theo trạng thái: pending | passed | watching | excluded | ignored.")
def radar_keywords(statuses: list[str] | None = None, regions: list[str] | None = None, limit: int = 100) -> list[dict]:
    from yt_niche.services import radar

    rows = radar.keywords(_db(), statuses or ["passed", "watching"], regions)
    keep = ("region", "keyword", "kind", "value", "status", "reason", "trend_class", "yt_score", "potential", "ai_type", "ai_vi", "seen_days", "last_seen")
    return [{k: r.get(k) for k in keep} for r in rows[:limit]]


@server.tool(description="Từ khoá đã lấy gợi ý / đã chấm trong bảng keyword theo từ gốc (seed) và thị trường. Không tốn quota.")
def keywords_list(seed: str | None = None, region: str | None = None, scored_only: bool = False, limit: int = 100) -> list[dict]:
    from yt_niche.services import keywords

    rows = keywords.list_keywords(_db(), seed, region.upper() if region else None)
    keep = ("region", "keyword", "seed", "source", "rank", "score", "demand", "competition", "n", "low_data", "scored_at", "run_id")
    out = [{k: r.get(k) for k in keep} for r in rows if not scored_only or r["score"] is not None]
    return out[:limit]


# ---------------------------------------------------------------------------
# Lấy dữ liệu thật
# ---------------------------------------------------------------------------


@server.tool(description="Gợi ý tìm kiếm YouTube cho một từ gốc (0 unit). az=true lấy thêm biến thể a–z. Trả về từ khoá kèm điểm nếu đã chấm trước đó.")
def keywords_suggest(seed: str, region: str = "VN", az: bool = False) -> dict | str:
    from yt_niche.services import keywords

    conn = _db()
    try:
        items = keywords.fetch_suggestions(conn, seed, region.upper(), az=az)
    except Exception as e:  # noqa: BLE001 — Claude cần đọc lỗi mạng / đầu vào
        return _err(e)
    rows = keywords.list_keywords(conn, seed, region.upper())
    return {
        "seed": seed, "region": region.upper(), "n_new": len(items),
        "keywords": [{k: r.get(k) for k in ("keyword", "source", "rank", "score", "demand", "competition", "n", "scored_at")} for r in rows],
    }


@server.tool(description="Chấm từ khoá bằng kết quả search YouTube thật (≈102 unit / từ, tối đa 10 từ). Từ khoá phải nằm trong bảng keyword (keywords_suggest trước, hoặc add_manual=true).")
def keywords_score(region: str, keywords_to_score: list[str], seed: str | None = None, add_manual: bool = False, force: bool = False) -> dict | str:
    from yt_niche.services import keywords

    conn = _db()
    region = region.upper()
    if not keywords_to_score:
        return "Lỗi: danh sách từ khoá trống"
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


@server.tool(description="Đào một ngách: search YouTube + video + kênh + Trends → điểm ngách, 3 tầng, kênh nhỏ nổ (≈210–350 unit). Có lần đào cùng thiết lập trong 90 ngày thì trả về lần đó thay vì đào lại, trừ khi fresh=true.")
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
        return f"Lỗi: thị trường {region} không có trong danh sách {list(cfg.ENABLED_MARKETS)}"
    if fmt not in ("any", "long", "shorts"):
        return "Lỗi: fmt phải là any | long | shorts"
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


@server.tool(description="Từ URL video / kênh YouTube: kênh và video tương tự (đối thủ), tiêu đề video thắng, tag kênh gốc (≈10–30 unit). region quyết định ngôn ngữ tìm kiếm: phải thuộc ngôn ngữ của ngách đang xét. Kết quả kèm country của từng kênh; kênh khác ngôn ngữ thì bỏ, không lấy làm đối thủ hay từ khoá.")
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


@server.tool(description="Quét lại kênh đã gắn vào ngách và kênh nhỏ nổ ở các lần đào của ngách: sub, trung vị view hiện tại (≈2 unit / kênh). Trả về bảng để Claude quyết định leader / rising / fallen.")
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
        return {"channels": [], "note": "Ngách chưa có kênh nào; gắn kênh bằng niche_set_channels hoặc đào trước."}
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
        "note": "language_mismatch=true: kênh khai quốc gia / ngôn ngữ khác ngách — cân nhắc bỏ khỏi đối thủ.",
    }


# ---------------------------------------------------------------------------
# Ghi quyết định
# ---------------------------------------------------------------------------


@server.tool(description=f"Tạo ngách. Một ngách = một ngôn ngữ: truyền language (vi, en, ja…) hoặc region (VN, US…) để suy ra. Từ khoá, lần đào, ngách mẹ, ngách gộp sau này đều phải cùng ngôn ngữ. motivation ∈ {list(nm.MOTIVATIONS)}. Ngách mới ở trạng thái draft cho tới khi có từ khoá main đã chấm hoặc một lần đào.")
def niche_create(
    name: str, rationale: str, language: str | None = None, region: str | None = None, parent_id: int | None = None,
    motivation: str | None = None, motivation_secondary: str | None = None, summary: str = "",
) -> dict | str:
    try:
        if not language:
            if not region:
                return "Lỗi: cần language (vi, en…) hoặc region (VN, US…) để biết ngách thuộc ngôn ngữ nào"
            language = nm.language_of_region(region)
        nid = nm.create(_db(), name, language=language, parent_id=parent_id, motivation=motivation,
                        motivation_secondary=motivation_secondary, summary=summary, rationale=rationale)
        return nm.get(_db(), nid)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Đổi tên / tóm tắt ngách.")
def niche_update(niche_id: int, rationale: str, name: str | None = None, summary: str | None = None) -> dict | str:
    try:
        return nm.update(_db(), niche_id, name=name, summary=summary, rationale=rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Gắn từ khoá vào ngách. items: [{keyword, region, kind: main|related|longtail|trend, source?}]. region phải cùng ngôn ngữ với ngách (xem regions_allowed trong niche_get). Số liệu copy từ bảng keyword / radar lúc gắn; kind=main chỉ nên dùng cho từ đã có số.")
def niche_attach_keywords(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.attach_keywords(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Bỏ từ khoá khỏi ngách. items: [{keyword, region}].")
def niche_detach_keywords(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.detach_keywords(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Gắn một lần đào (dig_run) vào ngách; lần đào phải cùng ngôn ngữ với ngách.")
def niche_attach_run(niche_id: int, run_id: int, rationale: str = "") -> dict | str:
    try:
        return nm.attach_run(_db(), niche_id, run_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Ghi kênh đối thủ của ngách. items: [{channel_id, role: leader|rising|fallen, note?}].")
def niche_set_channels(niche_id: int, items: list[dict], rationale: str) -> dict | str:
    try:
        return nm.set_channels(_db(), niche_id, items, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Chuyển ngách sang mẹ khác (new_parent_id bỏ trống = lên gốc).")
def niche_move(niche_id: int, rationale: str, new_parent_id: int | None = None) -> dict | str:
    try:
        return nm.move(_db(), niche_id, new_parent_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Gộp mềm source vào target (phải cùng ngôn ngữ): từ khoá, lần đào, kênh, ngách con chuyển sang target; source thành merged, tách lại được bằng niche_unmerge.")
def niche_merge(source_id: int, target_id: int, rationale: str) -> dict | str:
    try:
        return nm.merge(_db(), source_id, target_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Tách lại một ngách đã gộp.")
def niche_unmerge(niche_id: int, rationale: str) -> dict | str:
    try:
        return nm.unmerge(_db(), niche_id, rationale, actor="claude")
    except nm.NicheError as e:
        return _err(e)


@server.tool(description=f"Gán động cơ khán giả (chính + phụ tuỳ chọn). Các mã: {nm.MOTIVATIONS}")
def niche_set_motivation(niche_id: int, primary: str, rationale: str, secondary: str | None = None) -> dict | str:
    try:
        return nm.set_motivation(_db(), niche_id, primary, secondary, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Xếp tiềm năng 0–100 cho một hoặc nhiều ngách sau khi đọc map_compare. items: [{niche_id, potential, rationale}]. App lưu kèm ảnh chụp số liệu để báo khi hạng đã cũ.")
def niche_rank(items: list[dict], rationale: str = "") -> list[dict] | str:
    try:
        return nm.rank(_db(), items, rationale)
    except (nm.NicheError, KeyError, ValueError) as e:
        return _err(e)


@server.tool(description="Bỏ ngách (status=dropped, không xoá dữ liệu).")
def niche_drop(niche_id: int, rationale: str) -> dict | str:
    try:
        return nm.drop(_db(), niche_id, rationale)
    except nm.NicheError as e:
        return _err(e)


@server.tool(description="Chép lại số mới nhất từ bảng keyword / radar cho mọi từ khoá đã gắn vào ngách (sau khi chấm lại).")
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
