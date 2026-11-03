"""Dựng prompt (copy sang Claude/ChatGPT) và file Excel từ một lần đào."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from yt_niche.config import MARKETS
from yt_niche.core.scorecard import CRITERIA

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPTS = {"three_tier": "Chấm 3 tầng (bài 2.1)", "read_results": "Đọc kết quả đào (bài 2.2)"}
FMT_LABEL = {"any": "Mọi độ dài", "long": "Video dài", "shorts": "Shorts"}


def fmt_num(x: float | None) -> str:
    if x is None:
        return "không có"
    return f"{x:,.0f}".replace(",", ".")


def _context(run: dict, channel_style: str) -> str:
    p = run["result"]["params"]
    market = MARKETS.get(p["region"], ("", p["region"]))[1]
    return "\n".join(
        [
            f"- Chủ đề: {p['topic']}",
            f"- Thị trường: {market} ({p['region']}), ngôn ngữ {p['language']}",
            f"- Định dạng: {FMT_LABEL[p['fmt']]}; khoảng thời gian: {p['window_days']} ngày",
            f"- Nhóm chủ đề: {run.get('category') or 'chưa chọn'}",
            f"- Kiểu kênh dự định: {channel_style or 'chưa nêu'}",
        ]
    )


def data_block(run: dict) -> str:
    r = run["result"]
    t = r["trends"]
    income = r["income_per_video"]
    fs = r.get("format_split")
    lines = [
        f"- Điểm ngách: {r['score']:.0f}/100 ({', '.join(r['labels'])})",
        f"- Độ tin cậy: {r['confidence']} ({r['n']} video, {r['n_estimated']} số ước tính)",
        f"- 4 thanh con: Nhu cầu {r['demand']:.0f}, Cơ hội {r['opportunity']:.0f}, Độ mới {r['freshness']:.0f}, "
        f"Cạnh tranh {r['competition']:.0f}% (tỉ lệ video của kênh > 1 triệu sub)",
        f"- Trung vị lượt xem: {fmt_num(r['median_views'])}",
        f"- Video vượt trội (≥ 3×): {r['outlier_counts']['total']}; nổ view (≥ 10×): {r['outlier_counts']['viral']}; "
        f"siêu viral (≥ 50×): {r['outlier_counts']['super_viral']}",
        f"- Kênh nhỏ (< 100K sub) đang nổ: {len(r['small_boom_channels'])}",
        f"- Google Trends: {t['class'] or 'chưa có'}" + (f" ({t['reason']})" if t.get("reason") else ""),
        "- Thu nhập/video ≈ "
        + (f"{income[0]:g}–{income[1]:g} USD (ước tính theo trung vị × RPM)" if income else "chưa có RPM"),
        f"- Affiliate đã liệt kê: {', '.join(run['affiliates']) or 'chưa có'}",
    ]
    notes = run.get("channel_notes") or {}
    if notes:
        titles = {c["channel_id"]: c["title"] for c in r["small_boom_channels"]}
        parts = [
            f"{titles.get(cid, cid)}: {n['note'] or 'không ghi chú'}" + (" (re-up, không học theo)" if n.get("reup") else "")
            for cid, n in notes.items()
        ]
        lines.append(f"- Nhận xét kênh nhỏ nổ đã mở xem: {'; '.join(parts)}")
    if fs:
        lines.append(
            f"- Video dài: {fs['long']['n']} video, trung vị {fmt_num(fs['long']['median_views'])}, "
            f"{fs['long']['outliers']} vượt trội | Shorts: {fs['shorts']['n']} video, "
            f"trung vị {fmt_num(fs['shorts']['median_views'])}, {fs['shorts']['outliers']} vượt trội"
            + (f" → {fs['fit_label']}" if fs["fit_label"] else "")
        )
    return "\n".join(lines)


def build_prompt(name: str, run: dict, channel_style: str = "", top_n: int = 10) -> str:
    template = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    outliers = "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} view — {v['outlier']:.1f}× — {FMT_LABEL.get(v['fmt'], v['fmt'])}"
        for v in run["result"]["outliers"][:top_n]
    )
    return template.format(context=_context(run, channel_style), data=data_block(run), outliers=outliers or "(không có)")


def to_excel(run: dict) -> bytes:
    r = run["result"]
    summary = pd.DataFrame(
        [line.lstrip("- ").split(": ", 1) for line in data_block(run).splitlines()], columns=["Chỉ số", "Giá trị"]
    )
    outliers = pd.DataFrame(
        [
            {
                "Tiêu đề": v["title"],
                "Mức ×": round(v["outlier"], 1),
                "Nhãn": v["level"],
                "Lượt xem": v["views"],
                "Trung vị kênh": v["channel_median"],
                "Sub kênh": v["subs"],
                "Định dạng": v["fmt"],
                "Ngày đăng": v["published_at"][:10],
                "Link": f"https://www.youtube.com/watch?v={v['video_id']}",
            }
            for v in r["outliers"]
        ]
    )
    channels = pd.DataFrame(
        [
            {
                "Kênh": c["title"],
                "Sub": c["subs"],
                "Tuổi (tháng)": c["age_months"],
                "Số video": c["video_count"],
                "Video tốt nhất": c["best_title"],
                "Lượt xem": c["best_views"],
                "Mức ×": c["best_outlier"],
                "Link": f"https://www.youtube.com/channel/{c['channel_id']}",
            }
            for c in r["small_boom_channels"]
        ]
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Tóm tắt", index=False)
        outliers.to_excel(xw, sheet_name="Video vượt trội", index=False)
        channels.to_excel(xw, sheet_name="Kênh nhỏ nổ", index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Prompt bài 2.5
# ---------------------------------------------------------------------------


def _fill(name: str, **values) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").format(**values)


def build_scorecard_review_prompt(board: dict) -> str:
    """board: kết quả services.scorecards.load (đã khoá trọng số)."""
    weights = board["weights"]
    active = [c for c in CRITERIA if weights[c.key] > 0]
    weights_text = "\n".join(f"- {c.label}: {weights[c.key]}" for c in active)

    finals = {r["id"]: r["final"] for r in (board["decision"] or {}).get("ranking", [])}
    header = "| Ngách | " + " | ".join(c.label for c in active) + " | Điểm chốt |"
    sep = "|" + "---|" * (len(active) + 2)
    lines = [header, sep]
    for run in board["runs"]:
        cells = board["cells"][run["id"]]
        scores = " | ".join(str(cells[c.key]["score"] or "—") for c in active)
        final = finals.get(run["id"])
        lines.append(f"| {run['topic']} | {scores} | {'' if final is None else f'{final:g}'} |")

    rationale = []
    for run in board["runs"]:
        rationale.append(f"**{run['topic']}**")
        for c in active:
            cell = board["cells"][run["id"]][c.key]
            parts = [p for p in (cell["rationale"], cell["note"]) if p and p not in ("chấm tay",)]
            if parts:
                rationale.append(f"- {c.label}: {'; '.join(parts)}")
    data = "\n\n".join(f"**{run['topic']}**\n{data_block(run)}" for run in board["runs"])
    return _fill(
        "scorecard_review", weights=weights_text, table="\n".join(lines), rationale="\n".join(rationale), data=data
    )


def build_test_plan_prompt(
    board: dict,
    main_run: dict,
    *,
    per_week: int = 2,
    channel_style: str = "",
    top_n: int = 10,
    keywords: list[dict] | None = None,
) -> str:
    weeks = -(-10 // per_week)  # làm tròn lên
    outliers = "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} view — {v['outlier']:.1f}×"
        for v in main_run["result"]["outliers"][:top_n]
    )
    return _fill(
        "test_plan",
        niche=main_run["topic"],
        promise=board["promise"] or "(chưa viết)",
        context=_context(main_run, channel_style),
        data=data_block(main_run),
        outliers=outliers or "(không có)",
        weeks=weeks,
        per_week=per_week,
        keywords="\n".join(f"- {k['keyword']} ({k['score']:.0f} điểm)" for k in keywords or []) or "(chưa chấm)",
    )


# ---------------------------------------------------------------------------
# Prompt bài 2.3
# ---------------------------------------------------------------------------


def _winners(run: dict, top_n: int = 10) -> str:
    return "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} view — {v['outlier']:.1f}×" for v in run["result"]["outliers"][:top_n]
    ) or "(không có)"


def build_subniche_prompt(run: dict, cols: dict, phrases: list[dict], channel_style: str = "") -> str:
    """cols: core.subniche.columns(...); phrases: core.subniche.repeated_phrases(...)."""
    from yt_niche.core.subniche import LONG, SUBNICHE

    sub = "\n".join(f"- {x['keyword']} ({x['videos']} video thắng)" for x in cols[SUBNICHE][:15]) or "(không có)"
    longs = "\n".join(f"- {x['keyword']}" for x in cols[LONG][:20]) or "(không có)"
    ph = "\n".join(f"- {p['phrase']} ({p['videos']} video, {p['channels']} kênh)" for p in phrases) or "(không có)"
    return _fill(
        "subniche_expand",
        channel_style=channel_style or "không lộ mặt",
        context=_context(run, channel_style),
        data=data_block(run),
        subniches=sub,
        phrases=ph,
        long_keywords=longs,
    )


def build_ideas_prompt(run: dict, promise: str, keywords: list[dict], channel_style: str = "") -> str:
    kws = "\n".join(
        f"- {k['keyword']}: {k['score']:.0f}" + (" (ít dữ liệu)" if k["low_data"] else "")
        for k in keywords
        if k.get("score") is not None
    ) or "(chưa chấm)"
    return _fill(
        "ideas_30",
        niche=run["topic"],
        promise=promise or "(chưa viết)",
        context=_context(run, channel_style),
        keywords=kws,
        winners=_winners(run),
    )


# ---------------------------------------------------------------------------
# Prompt bài 2.4
# ---------------------------------------------------------------------------


def compare_table(cmp: dict) -> str:
    """Bảng 7 cột dạng markdown từ core.compare.compare (gồm cả phương án tin cậy thấp, có đánh dấu)."""
    lines = [
        "| Ngách | Điểm + nhãn | Độ tin cậy | Lượt xem trung vị | Vượt trội / kênh nhỏ nổ | Trends | Thu nhập/video (≈) | Định dạng thắng |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in cmp["rows"] + cmp["excluded"]:
        income = f"{r['income'][0]:g}–{r['income'][1]:g} USD" if r["income"] else "chưa có RPM"
        trend = r["trend_class"] or "chưa có"
        if r["trend_class"]:
            trend += f" (12 tháng {r['trend_12m']:+.0%}, 5 năm {r['trend_5y']:+.0%})"
        lines.append(
            f"| {r['name']} | {r['score']:.0f} ({r['label']}) | {r['confidence']} ({r['n']} video) | "
            f"{fmt_num(r['median_views'])} | {r['outliers']} / {r['small_channels']} | {trend} | {income} | "
            f"{FMT_LABEL.get(r['format_fit'], 'chưa rõ') if r['format_fit'] else 'chưa rõ'} |"
        )
    return "\n".join(lines)


def build_compare_prompt(cmp: dict, runs: list[dict], ability: str = "") -> str:
    first = runs[0]
    setup = f"{MARKETS.get(first['region'], ('', first['region']))[1]}, {FMT_LABEL[first['fmt']]}, {first['window_days']} ngày"
    notes = list(cmp["warnings"])
    if cmp["view_ratio"]:
        notes.append(f"Tỉ lệ lượt xem trung vị cao nhất / thấp nhất: {cmp['view_ratio']}×")
    notes += [f"Ngách điểm khá nhưng ít kênh nhỏ nổ (có thể là ngách của kênh lớn): {n}" for n in cmp["big_channel_niches"]]
    return _fill(
        "compare_niches",
        ability=ability or "(chưa nêu)",
        setup=setup,
        table=compare_table(cmp),
        notes="\n".join(f"- {n}" for n in notes) or "- (không có)",
    )


def build_market_compare_prompt(batch: dict, ability: str = "") -> str:
    lines = [
        "| Thị trường | Từ khoá | Điểm cơ hội | Điểm ngách | Độ tin cậy | Lượt xem trung vị | Kênh nhỏ nổ | RPM (≈ USD) | Thu nhập/video (≈) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for it in batch["items"]:
        if not it.get("result"):
            continue
        r, o = it["result"], it["opportunity"]
        rpm = f"{r['rpm'][0]:g}–{r['rpm'][1]:g}" if r.get("rpm") else "chưa có"
        income = f"{r['income_per_video'][0]:g}–{r['income_per_video'][1]:g} USD" if r.get("income_per_video") else "chưa có"
        lines.append(
            f"| {MARKETS[it['region']][1]} | {it['keyword']} | {o['score'] if o['score'] is not None else '—'} | "
            f"{r['score']:.0f} | {r['confidence']} | {fmt_num(r['median_views'])} | {len(r['small_boom_channels'])} | "
            f"{rpm} | {income} |"
        )
    setup = f"chủ đề gốc \"{batch['topic']}\", {FMT_LABEL[batch['fmt']]}, {batch['window_days']} ngày, đào cùng ngày"
    formula = "Điểm cơ hội = 45% điểm ngách + 30% tìm kiếm + 25% kiếm tiền; chưa có Trends thì 60% điểm ngách + 40% giá quảng cáo"
    return _fill(
        "compare_niches", ability=ability or "(chưa nêu)", setup=setup, table="\n".join(lines), notes=f"- {formula}"
    )


def build_native_prompt(item: dict, topic: str) -> str:
    """item: một phần tử của services.markets.load_batch()["items"] đã có kết quả."""
    run = {"result": item["result"], "affiliates": [], "category": item["run"].get("category")}
    return _fill(
        "native_check",
        market_name=MARKETS[item["region"]][1],
        topic=topic,
        keyword=item["keyword"],
        data=data_block(run),
        winners=_winners(run),
    )


def build_translate_prompt(topic: str, regions: list[str]) -> str:
    markets = "\n".join(f"- {r}: {MARKETS[r][1]} (ngôn ngữ {MARKETS[r][0]})" for r in regions)
    fmt = "\n".join(f"{r}: <từ khoá>" for r in regions)
    return _fill("translate_keywords", topic=topic, markets=markets, format=fmt)


def parse_translations(text: str) -> dict[str, str]:
    """Đọc các dòng "US: morning prayer" (chấp nhận gạch đầu dòng, dấu ngoặc kép)."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if ":" not in line:
            continue
        code, kw = line.split(":", 1)
        code = code.strip().upper()
        kw = kw.strip().strip("\"'“”`").strip()
        if code in MARKETS and kw:
            out[code] = kw
    return out
