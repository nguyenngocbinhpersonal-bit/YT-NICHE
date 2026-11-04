"""Build prompts (to paste into Claude/ChatGPT) and the Excel file for one dig."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from yt_niche.config import MARKETS
from yt_niche.core.scorecard import CRITERIA

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPTS = {"three_tier": "Three-tier check (lesson 2.1)", "read_results": "Read dig results (lesson 2.2)"}
FMT_LABEL = {"any": "Any length", "long": "Long-form", "shorts": "Shorts"}


def fmt_num(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:,.0f}".replace(",", ".")


def _context(run: dict, channel_style: str) -> str:
    p = run["result"]["params"]
    market = MARKETS.get(p["region"], ("", p["region"]))[1]
    return "\n".join(
        [
            f"- Topic: {p['topic']}",
            f"- Market: {market} ({p['region']}), language {p['language']}",
            f"- Format: {FMT_LABEL[p['fmt']]}; time window: {p['window_days']} days",
            f"- Topic category: {run.get('category') or 'not chosen'}",
            f"- Planned channel style: {channel_style or 'not stated'}",
        ]
    )


def data_block(run: dict) -> str:
    r = run["result"]
    t = r["trends"]
    income = r["income_per_video"]
    fs = r.get("format_split")
    lines = [
        f"- Niche score: {r['score']:.0f}/100 ({', '.join(r['labels'])})",
        f"- Confidence: {r['confidence']} ({r['n']} videos, {r['n_estimated']} estimated numbers)",
        f"- Four bars: Demand {r['demand']:.0f}, Opportunity {r['opportunity']:.0f}, Freshness {r['freshness']:.0f}, "
        f"Competition {r['competition']:.0f}% (share of videos from channels > 1M subs)",
        f"- Median views: {fmt_num(r['median_views'])}",
        f"- Outperformer videos (≥ 3×): {r['outlier_counts']['total']}; viral (≥ 10×): {r['outlier_counts']['viral']}; "
        f"super viral (≥ 50×): {r['outlier_counts']['super_viral']}",
        f"- Small booming channels (< 100K subs): {len(r['small_boom_channels'])}",
        f"- Google Trends: {t['class'] or 'n/a'}" + (f" ({t['reason']})" if t.get("reason") else ""),
        "- Income per video ≈ "
        + (f"{income[0]:g}–{income[1]:g} USD (estimate: median × RPM)" if income else "no RPM yet"),
        f"- Affiliates listed: {', '.join(run['affiliates']) or 'none yet'}",
    ]
    notes = run.get("channel_notes") or {}
    if notes:
        titles = {c["channel_id"]: c["title"] for c in r["small_boom_channels"]}
        parts = [
            f"{titles.get(cid, cid)}: {n['note'] or 'no note'}" + (" (re-upload, do not copy)" if n.get("reup") else "")
            for cid, n in notes.items()
        ]
        lines.append(f"- Notes on small booming channels you opened: {'; '.join(parts)}")
    if fs:
        lines.append(
            f"- Long-form: {fs['long']['n']} videos, median {fmt_num(fs['long']['median_views'])}, "
            f"{fs['long']['outliers']} outperformers | Shorts: {fs['shorts']['n']} videos, "
            f"median {fmt_num(fs['shorts']['median_views'])}, {fs['shorts']['outliers']} outperformers"
            + (f" → {fs['fit_label']}" if fs["fit_label"] else "")
        )
    return "\n".join(lines)


def build_prompt(name: str, run: dict, channel_style: str = "", top_n: int = 10) -> str:
    template = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    outliers = "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} views — {v['outlier']:.1f}× — {FMT_LABEL.get(v['fmt'], v['fmt'])}"
        for v in run["result"]["outliers"][:top_n]
    )
    return template.format(context=_context(run, channel_style), data=data_block(run), outliers=outliers or "(none)")


def to_excel(run: dict) -> bytes:
    r = run["result"]
    summary = pd.DataFrame(
        [line.lstrip("- ").split(": ", 1) for line in data_block(run).splitlines()], columns=["Metric", "Value"]
    )
    outliers = pd.DataFrame(
        [
            {
                "Title": v["title"],
                "Multiple ×": round(v["outlier"], 1),
                "Label": v["level"],
                "Views": v["views"],
                "Channel median": v["channel_median"],
                "Channel subs": v["subs"],
                "Format": v["fmt"],
                "Published": v["published_at"][:10],
                "Link": f"https://www.youtube.com/watch?v={v['video_id']}",
            }
            for v in r["outliers"]
        ]
    )
    channels = pd.DataFrame(
        [
            {
                "Channel": c["title"],
                "Subs": c["subs"],
                "Age (months)": c["age_months"],
                "Videos": c["video_count"],
                "Best video": c["best_title"],
                "Views": c["best_views"],
                "Multiple ×": c["best_outlier"],
                "Link": f"https://www.youtube.com/channel/{c['channel_id']}",
            }
            for c in r["small_boom_channels"]
        ]
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Summary", index=False)
        outliers.to_excel(xw, sheet_name="Outperformer videos", index=False)
        channels.to_excel(xw, sheet_name="Small booming channels", index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Lesson 2.5 prompts
# ---------------------------------------------------------------------------


def _fill(name: str, **values) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").format(**values)


def build_scorecard_review_prompt(board: dict) -> str:
    """board: result of services.scorecards.load (weights locked)."""
    weights = board["weights"]
    active = [c for c in CRITERIA if weights[c.key] > 0]
    weights_text = "\n".join(f"- {c.label}: {weights[c.key]}" for c in active)

    finals = {r["id"]: r["final"] for r in (board["decision"] or {}).get("ranking", [])}
    header = "| Niche | " + " | ".join(c.label for c in active) + " | Final score |"
    sep = "|" + "---|" * (len(active) + 2)
    lines = [header, sep]
    for run in board["runs"]:
        cells = board["cells"][run["id"]]
        scores = " | ".join(str(cells[c.key]["score"] or "—") for c in active)
        final = finals.get(run["id"])
        lines.append(f"| {run['topic']} | {scores} | {'' if final is None else f'{final:g}'} |")

    from yt_niche.services.scorecards import _MANUAL_RATIONALES

    rationale = []
    for run in board["runs"]:
        rationale.append(f"**{run['topic']}**")
        for c in active:
            cell = board["cells"][run["id"]][c.key]
            parts = [p for p in (cell["rationale"], cell["note"]) if p and p not in _MANUAL_RATIONALES]
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
    weeks = -(-10 // per_week)  # round up
    outliers = "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} views — {v['outlier']:.1f}×"
        for v in main_run["result"]["outliers"][:top_n]
    )
    return _fill(
        "test_plan",
        niche=main_run["topic"],
        promise=board["promise"] or "(not written yet)",
        context=_context(main_run, channel_style),
        data=data_block(main_run),
        outliers=outliers or "(none)",
        weeks=weeks,
        per_week=per_week,
        keywords="\n".join(f"- {k['keyword']} (score {k['score']:.0f})" for k in keywords or []) or "(not scored yet)",
    )


# ---------------------------------------------------------------------------
# Lesson 2.3 prompts
# ---------------------------------------------------------------------------


def _winners(run: dict, top_n: int = 10) -> str:
    return "\n".join(
        f"- {v['title']} — {fmt_num(v['views'])} views — {v['outlier']:.1f}×" for v in run["result"]["outliers"][:top_n]
    ) or "(none)"


def build_subniche_prompt(run: dict, cols: dict, phrases: list[dict], channel_style: str = "") -> str:
    """cols: core.subniche.columns(...); phrases: core.subniche.repeated_phrases(...)."""
    from yt_niche.core.subniche import LONG, SUBNICHE

    sub = "\n".join(f"- {x['keyword']} ({x['videos']} winning videos)" for x in cols[SUBNICHE][:15]) or "(none)"
    longs = "\n".join(f"- {x['keyword']}" for x in cols[LONG][:20]) or "(none)"
    ph = "\n".join(f"- {p['phrase']} ({p['videos']} videos, {p['channels']} channels)" for p in phrases) or "(none)"
    return _fill(
        "subniche_expand",
        channel_style=channel_style or "faceless",
        context=_context(run, channel_style),
        data=data_block(run),
        subniches=sub,
        phrases=ph,
        long_keywords=longs,
    )


def build_ideas_prompt(run: dict, promise: str, keywords: list[dict], channel_style: str = "") -> str:
    kws = "\n".join(
        f"- {k['keyword']}: {k['score']:.0f}" + (" (low data)" if k["low_data"] else "")
        for k in keywords
        if k.get("score") is not None
    ) or "(not scored yet)"
    return _fill(
        "ideas_30",
        niche=run["topic"],
        promise=promise or "(not written yet)",
        context=_context(run, channel_style),
        keywords=kws,
        winners=_winners(run),
    )


# ---------------------------------------------------------------------------
# Lesson 2.4 prompts
# ---------------------------------------------------------------------------


def compare_table(cmp: dict) -> str:
    """7-column markdown table from core.compare.compare (including low-confidence options, marked)."""
    lines = [
        "| Niche | Score + label | Confidence | Median views | Outperformers / small booming | Trends | Income per video (≈) | Winning format |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in cmp["rows"] + cmp["excluded"]:
        income = f"{r['income'][0]:g}–{r['income'][1]:g} USD" if r["income"] else "no RPM yet"
        trend = r["trend_class"] or "n/a"
        if r["trend_class"]:
            trend += f" (12 months {r['trend_12m']:+.0%}, 5 years {r['trend_5y']:+.0%})"
        lines.append(
            f"| {r['name']} | {r['score']:.0f} ({r['label']}) | {r['confidence']} ({r['n']} videos) | "
            f"{fmt_num(r['median_views'])} | {r['outliers']} / {r['small_channels']} | {trend} | {income} | "
            f"{FMT_LABEL.get(r['format_fit'], 'unclear') if r['format_fit'] else 'unclear'} |"
        )
    return "\n".join(lines)


def build_compare_prompt(cmp: dict, runs: list[dict], ability: str = "") -> str:
    first = runs[0]
    setup = f"{MARKETS.get(first['region'], ('', first['region']))[1]}, {FMT_LABEL[first['fmt']]}, {first['window_days']} days"
    notes = list(cmp["warnings"])
    if cmp["view_ratio"]:
        notes.append(f"Highest / lowest median views ratio: {cmp['view_ratio']}×")
    notes += [f"Decent score but few small booming channels (may be a big-channel niche): {n}" for n in cmp["big_channel_niches"]]
    return _fill(
        "compare_niches",
        ability=ability or "(not stated)",
        setup=setup,
        table=compare_table(cmp),
        notes="\n".join(f"- {n}" for n in notes) or "- (none)",
    )


def build_market_compare_prompt(batch: dict, ability: str = "") -> str:
    lines = [
        "| Market | Keyword | Opportunity score | Niche score | Confidence | Median views | Small booming channels | RPM (≈ USD) | Income per video (≈) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for it in batch["items"]:
        if not it.get("result"):
            continue
        r, o = it["result"], it["opportunity"]
        rpm = f"{r['rpm'][0]:g}–{r['rpm'][1]:g}" if r.get("rpm") else "n/a"
        income = f"{r['income_per_video'][0]:g}–{r['income_per_video'][1]:g} USD" if r.get("income_per_video") else "n/a"
        lines.append(
            f"| {MARKETS[it['region']][1]} | {it['keyword']} | {o['score'] if o['score'] is not None else '—'} | "
            f"{r['score']:.0f} | {r['confidence']} | {fmt_num(r['median_views'])} | {len(r['small_boom_channels'])} | "
            f"{rpm} | {income} |"
        )
    setup = f"source topic \"{batch['topic']}\", {FMT_LABEL[batch['fmt']]}, {batch['window_days']} days, all dug on the same day"
    formula = "Opportunity score = 45% niche score + 30% search + 25% money; without Trends: 60% niche score + 40% ad price"
    return _fill(
        "compare_niches", ability=ability or "(not stated)", setup=setup, table="\n".join(lines), notes=f"- {formula}"
    )


def build_native_prompt(item: dict, topic: str) -> str:
    """item: one element of services.markets.load_batch()["items"] that has a result."""
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
    markets = "\n".join(f"- {r}: {MARKETS[r][1]} (language {MARKETS[r][0]})" for r in regions)
    fmt = "\n".join(f"{r}: <keyword>" for r in regions)
    return _fill("translate_keywords", topic=topic, markets=markets, format=fmt)


def parse_translations(text: str) -> dict[str, str]:
    """Read lines like "US: morning prayer" (bullets and quotes are accepted)."""
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
