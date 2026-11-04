"""Compare page (lesson 2.4): compare 2–5 niches in a 7-column table, and dig one topic across several markets."""

import asyncio

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import compare as cmp_core
from yt_niche.db import runs as run_repo
from yt_niche.services import ai, markets as mk_svc
from yt_niche.services import report
from yt_niche.services import scorecards as sc_svc
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.suggest import suggestions
from yt_niche.ui import theme
from yt_niche.ui.common import NO_CATEGORY, fmt_num, get_conn, market_label, prompt_box, render_run, run_label

conn = get_conn()
theme.inject_css()
BEST = " 🟩"  # marks the best cell of each column
SOURCE_MARKET = "VN"  # the multi-market batch starts from a source topic in this market

theme.page_header("🌍", "Compare", "Same topic across markets, side by side.")

tab_niche, tab_market, tab_prompt = st.tabs(["Compare niches", "Multi-market", "Prompts"])

# ---------------------------------------------------------------------------
# Compare niches
# ---------------------------------------------------------------------------

with tab_niche:
    all_runs = run_repo.list_all(conn)
    by_id = {r["id"]: r for r in all_runs}
    theme.section(
        "Pick the digs to compare",
        "Only compare digs made with the same setup on the same day. A gap of 10 points is clear; under 5 is a tie; 2–3 is noise.",
    )
    if len(all_runs) < 2:
        theme.empty_state("At least 2 digs are needed.", "Dig more on the Dig page or the Sub-niches & keywords page.")
        cmp, picked_runs, ability_text = None, [], ""
    else:
        default = [r["id"] for r in all_runs if r["shortlisted"]][:5] or [r["id"] for r in all_runs[:2]]
        with st.container(border=True):
            picked = st.multiselect(
                "Digs (2–5; ideally sub-niches dug with the same setup, same day)",
                list(by_id),
                default=default,
                format_func=lambda i: run_label(by_id[i]),
                max_selections=5,
            )
            c1, c2 = st.columns([1, 2])
            ability = c1.radio(
                "Format you can make",
                list(cmp_core.ABILITY),
                format_func=cmp_core.ABILITY.get,
                horizontal=True,
                index=2,
            )
            ability_text = c2.text_input(
                "Your skills and constraints (goes into the prompt)", placeholder="e.g. faceless, 2 videos a week, can write English"
            )
        picked_runs = [by_id[i] for i in picked]
        cmp = cmp_core.compare(picked_runs, ability) if len(picked_runs) >= 2 else None

    if cmp:
        if cmp["warnings"]:
            theme.pills(*[theme.pill(f"⚠ {w}", "amber") for w in cmp["warnings"]])
        for r in cmp["excluded"]:
            st.error(f"Left out of the ranking: {r['name']} (low confidence, {r['n']} videos). Dig it again for a bigger sample.")

        best = cmp["best"]

        def mark(rid: int, col: str, text: str) -> str:
            return text + (BEST if rid in best[col] else "")

        rows = []
        for r in cmp["rows"]:
            income = f"{r['income'][0]:g}–{r['income'][1]:g} USD" if r["income"] else "no RPM yet"
            trend = r["trend_class"] or "none yet"
            if r["trend_class"]:
                trend += f" (1y {r['trend_12m']:+.0%}, 5y {r['trend_5y']:+.0%})"
            fit = report.FMT_LABEL.get(r["format_fit"], "unclear") if r["format_fit"] else "unclear"
            rows.append(
                {
                    "Niche": r["name"],
                    cmp_core.COLUMNS["score"]: mark(r["id"], "score", f"{r['score']:.0f} · {r['label']}"),
                    cmp_core.COLUMNS["confidence"]: mark(r["id"], "confidence", f"{r['confidence']} ({r['n']})"),
                    cmp_core.COLUMNS["median_views"]: mark(r["id"], "median_views", fmt_num(r["median_views"])),
                    cmp_core.COLUMNS["doors"]: mark(r["id"], "doors", f"{r['outliers']} / {r['small_channels']}"),
                    cmp_core.COLUMNS["trends"]: mark(r["id"], "trends", trend),
                    cmp_core.COLUMNS["income"]: mark(r["id"], "income", income),
                    cmp_core.COLUMNS["format"]: mark(r["id"], "format", fit),
                }
            )
        theme.section("Seven columns", f"{BEST.strip()} marks the best cell of each column. Winning format is marked when it fits the format you can make.")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Score gap per pair**")
            if cmp["pairs"]:
                st.dataframe(
                    pd.DataFrame([{"Pair": f"{p['a']} – {p['b']}", "Gap": p["gap"], "Read as": p["verdict"]} for p in cmp["pairs"]]),
                    hide_index=True,
                    width="stretch",
                )
        with c2:
            if cmp["view_ratio"]:
                st.metric("Median views: highest ÷ lowest", f"{cmp['view_ratio']:g}×")
            if cmp["big_channel_niches"]:
                theme.pills(*[theme.pill(f"⚠ {name}: decent score but few small booming channels", "amber") for name in cmp["big_channel_niches"]])
                st.caption("A good score with few small booming channels may be a big-channel niche.")

        if cmp["leader"]:
            theme.section("Verdict", "The app's suggestion. Rewrite it in your own words before deciding.")
            with st.container(border=True):
                st.markdown(f"🏆 **Leader: {cmp['leader']['name']}**")
                st.markdown("**Three-sentence verdict**")
                st.markdown(
                    f"1. **{cmp['leader']['name']}** leads: {cmp['leader_reason']}.\n"
                    f"2. Differentiating column: {cmp['differentiator']}.\n"
                    f"3. Biggest risk: {cmp['risk']}."
                )
            for d in cmp["doors"]:
                st.caption(f"🚪 Door left open by a losing niche: {d}. Do not drop it without opening its booming-channel frame.")

            with st.form("cmp_board"):
                name = st.text_input("Scorecard name", value="Compare " + pd.Timestamp.now().strftime("%d/%m"))
                if st.form_submit_button("⚖️ Create a scorecard from these niches", type="primary"):
                    ids = [r["id"] for r in cmp["rows"]]
                    try:
                        st.session_state["board"] = sc_svc.create(conn, name, ids)
                        st.success("Scorecard created. Open the Scorecard page to lock the weights and score.")
                    except sc_svc.ScorecardError as e:
                        st.error(str(e))

# ---------------------------------------------------------------------------
# Multi-market
# ---------------------------------------------------------------------------

with tab_market:
    batches = mk_svc.list_batches(conn)
    NEW = 0
    options = [NEW] + [b["id"] for b in batches]
    labels = {NEW: "➕ New batch"} | {
        b["id"]: f"{b['topic']} · {b['n_done'] or 0}/{b['n']} markets · {b['created_at'][:10]} (#{b['id']})" for b in batches
    }
    current = st.session_state.get("batch")
    batch_id = st.selectbox(
        "Multi-market batch", options, index=options.index(current) if current in options else (1 if batches else 0),
        format_func=labels.get,
    )
    if batch_id != NEW:
        st.session_state["batch"] = batch_id

    if batch_id == NEW:
        theme.section(
            "Dig one topic in several markets",
            f"Pick 6–10 markets, translate the keyword the way natives type it, then dig. Up to {mk_svc.MAX_MARKETS} markets per batch.",
        )
        with st.container(border=True):
            topic = st.text_input("Source topic (Vietnamese)", placeholder="morning prayer", key="mk_topic")
            g = st.columns(len(cfg.MARKET_GROUPS))
            groups = [name for col, name in zip(g, cfg.MARKET_GROUPS) if col.checkbox(name, key=f"grp_{name}")]
            from_groups = [code for name in groups for code in cfg.MARKET_GROUPS[name]]
            regions = st.multiselect(
                f"Markets (6–10, up to {mk_svc.MAX_MARKETS})",
                cfg.ENABLED_MARKETS,
                default=[m for m in dict.fromkeys([SOURCE_MARKET] + from_groups) if m in cfg.ENABLED_MARKETS][: mk_svc.MAX_MARKETS],
                format_func=market_label,
                max_selections=mk_svc.MAX_MARKETS,
                key=f"mk_regions_{'_'.join(groups)}",
            )

            with st.expander("Translate the keyword: copy the prompt to Claude / ChatGPT and paste the answer here", expanded=bool(topic)):
                st.caption("Do not translate word for word: you need the phrase natives actually type on YouTube.")
                if topic and regions:
                    tr_prompt = report.build_translate_prompt(topic, [r for r in regions if r != SOURCE_MARKET] or regions)
                    st.code(tr_prompt, language="markdown")
                    if ai.enabled(conn, "prompts") and st.button("🤖 Translate with AI (fills the box below)", key="mk_ai_translate"):
                        with st.spinner("AI is translating…"):
                            try:
                                answer = ai.run_prompt(conn, tr_prompt, tier="fast").strip().strip("`").strip()
                                if report.parse_translations(answer):
                                    st.session_state["mk_paste"] = answer
                                else:
                                    st.error(f"The AI answer is not in the \"US: keyword\" form: {answer[:200]}")
                            except ai.AIError as e:
                                st.error(str(e))
                pasted = st.text_area("Paste the answer (one line per market, \"US: keyword\")", key="mk_paste", height=120)
            translations = report.parse_translations(pasted)
            if topic:
                translations.setdefault(SOURCE_MARKET, topic)

            editor = st.data_editor(
                pd.DataFrame({"Market": regions, "Keyword": [translations.get(r, "") for r in regions]}),
                hide_index=True,
                width="stretch",
                disabled=["Market"],
                key=f"mk_kw_{hash((tuple(regions), pasted, topic))}",
            )
            keywords = {row["Market"]: str(row["Keyword"] or "") for _, row in editor.iterrows()}

            if st.button("🔎 Check that people type these keywords (YouTube suggestions, free)", disabled=not any(keywords.values())):

                async def _check():
                    return await asyncio.gather(
                        *(suggestions(conn, kw, r, cfg.MARKETS[r][0]) for r, kw in keywords.items() if kw.strip())
                    )

                try:
                    results = asyncio.run(_check())
                    for (r, kw), sug in zip([(r, kw) for r, kw in keywords.items() if kw.strip()], results):
                        top = ", ".join(s["keyword"] for s in sug[:4]) or "no suggestions"
                        icon = "🟢" if sug else "🔴"
                        st.markdown(f"{icon} **{market_label(r)}** \"{kw}\": {top}")
                except Exception as e:
                    st.error(f"Could not fetch suggestions: {e}")

            m1, m2, m3, m4 = st.columns(4)
            category = m1.selectbox("Topic category (RPM)", [NO_CATEGORY] + rpm_source.categories(), key="mk_cat")
            window_days = m2.selectbox("Time window", [7, 30, 90, 365], index=1, format_func=lambda d: f"{d} days", key="mk_win")
            fmt = m3.selectbox("Format", list(report.FMT_LABEL), format_func=report.FMT_LABEL.get, key="mk_fmt")
            pages = m4.selectbox("Search pages", [1, 2], key="mk_pages", help="1 page ≈ 200 units per market, 2 pages ≈ 320")
            with_trends = st.checkbox(
                "Include Google Trends per market (often rate-limited with many markets; without it the 60/40 formula is used)",
                key="mk_trends",
            )
            n = sum(1 for k in keywords.values() if k.strip())
            if category == NO_CATEGORY:
                st.warning("Pick a topic category to get RPM; without RPM the opportunity score cannot be computed.")
            st.caption(f"Estimated {mk_svc.estimate_units(n, pages):,} units for {n} markets (less when channels are already cached).")
            if st.button(f"⛏️ Dig {n} markets", type="primary", disabled=n < 2 or not cfg.api_key()):
                try:
                    bid = mk_svc.create_batch(
                        conn, topic, keywords, category=None if category == NO_CATEGORY else category,
                        window_days=window_days, fmt=fmt,
                    )
                except mk_svc.MarketError as e:
                    st.error(str(e))
                else:
                    bar = st.progress(0.0, text="Starting…")
                    out = mk_svc.run_batch(
                        conn, bid, pages=pages, with_trends=with_trends,
                        on_progress=lambda i, total, r: bar.progress(i / total, text=f"Digging {market_label(r)} ({i + 1}/{total})"),
                    )
                    if out["stopped"]:
                        st.error(out["stopped"])
                    st.session_state["batch"] = bid
                    st.rerun()
    else:
        batch = mk_svc.load_batch(conn, batch_id)
        st.caption(
            f"Source topic \"{batch['topic']}\" · {report.FMT_LABEL[batch['fmt']]} · {batch['window_days']} days · "
            f"category {batch['category'] or 'none'} · dug on {batch['created_at'][:10]}"
        )
        done = [it for it in batch["items"] if it.get("result")]
        missing = [it for it in batch["items"] if not it.get("result")]
        scored = [it for it in done if it["opportunity"]["score"] is not None]
        green = [it for it in scored if it["opportunity"].get("green")]
        top_it = max(scored, key=lambda it: it["opportunity"]["score"]) if scored else None
        theme.kpi_row([
            ("Markets dug", f"{len(done)}/{len(batch['items'])}", f"{len(missing)} missing" if missing else "complete"),
            ("Best opportunity", f"{top_it['opportunity']['score']:.0f}" if top_it else "—", market_label(top_it["region"]) if top_it else "needs RPM"),
            ("Green zone", len(green), f"opportunity ≥ {cfg.OPPORTUNITY_GREEN_MIN}"),
            ("Best niche score", f"{max(it['result']['score'] for it in done):.0f}" if done else "—", None),
        ])
        rows = []
        for it in done:
            r, o = it["result"], it["opportunity"]
            ch = it["sample_channel"]
            rows.append(
                {
                    "Market": market_label(it["region"]),
                    "Keyword": it["keyword"],
                    "Opportunity": o["score"],
                    "Zone": ("🟢 green" if o.get("green") else "") if o["score"] is not None else o["formula"],
                    "Niche score": round(r["score"]),
                    "Confidence": r["confidence"],
                    "Median views": r["median_views"],
                    "Small booming": len(r["small_boom_channels"]),
                    "RPM (≈ USD)": f"{r['rpm'][0]:g}–{r['rpm'][1]:g}" if r.get("rpm") else "",
                    "Income per video (≈ USD)": f"{r['income_per_video'][0]:g}–{r['income_per_video'][1]:g}" if r.get("income_per_video") else "",
                    "Winning sub-topics": ", ".join(it["topics"][:3]),
                    "Sample channel": (
                        f"{ch['title']} · {ch['age_months']} months · {fmt_num(ch['subs'])} subs · {fmt_num(ch['best_views'])} views"
                        if ch
                        else ""
                    ),
                }
            )
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                column_config={
                    "Opportunity": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                    "Niche score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
                    "Median views": st.column_config.NumberColumn(format="localized"),
                },
            )
            formulas = {it["opportunity"]["formula"] for it in done if it["opportunity"]["score"] is not None}
            with st.expander("How the opportunity score is computed"):
                st.markdown(
                    f"Opportunity = {' | '.join(sorted(formulas))}. From {cfg.OPPORTUNITY_GREEN_MIN} up is the green zone. "
                    "A high opportunity score does not mean you can make it: also score Sustainable production on the Scorecard. "
                    "Estimated income is only for relative comparison, not a salary figure."
                )
        for it in missing:
            st.caption(f"⏳ {market_label(it['region'])} \"{it['keyword']}\": {it['error'] or 'not dug yet'}")
        b1, b2, _ = st.columns([1, 1, 2])
        if missing and b1.button(f"Dig the {len(missing)} missing markets", type="primary", disabled=not cfg.api_key(), width="stretch"):
            out = mk_svc.run_batch(conn, batch_id)
            if out["stopped"]:
                st.error(out["stopped"])
            st.rerun()
        if b2.button("🗑️ Delete this batch", width="stretch"):
            mk_svc.delete_batch(conn, batch_id)
            st.session_state.pop("batch", None)
            st.rerun()

        if done:
            detail = st.selectbox(
                "Open one market in full (sub-topics, winning videos, booming channels)",
                [None] + [it["run_id"] for it in done],
                format_func=lambda i: "—" if i is None else next(market_label(it["region"]) for it in done if it["run_id"] == i),
            )
            if detail:
                st.divider()
                render_run(run_repo.get(conn, detail, with_raw=True))

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

with tab_prompt:
    theme.section("Prompts for Claude / ChatGPT", "Copy a prompt, or run it in the app when AI is on in Settings.")
    st.markdown("**Compare 2–3 niches from the table**")
    if cmp:
        prompt_box(report.build_compare_prompt(cmp, picked_runs, ability_text))
    else:
        st.caption("Pick at least 2 digs in the Compare niches tab.")
    st.markdown("**Compare markets from the table**")
    batch_for_prompt = mk_svc.load_batch(conn, st.session_state["batch"]) if st.session_state.get("batch") else None
    if batch_for_prompt and any(it.get("result") for it in batch_for_prompt["items"]):
        prompt_box(report.build_market_compare_prompt(batch_for_prompt, ability_text))
        st.markdown("**Check whether you can make native-quality content**")
        items = [it for it in batch_for_prompt["items"] if it.get("result") and it["region"] != SOURCE_MARKET]
        if items:
            region = st.selectbox("Market", [it["region"] for it in items], format_func=market_label, key="native_r")
            item = next(it for it in items if it["region"] == region)
            prompt_box(report.build_native_prompt(item, batch_for_prompt["topic"]))
    else:
        st.caption("Pick a multi-market batch with results in the Multi-market tab.")
