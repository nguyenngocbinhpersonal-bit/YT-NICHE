"""Sub-niches & keywords page (lesson 2.3): go one layer down, check the sub-niche is still alive, score long-tail keywords."""

import asyncio

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import keyword_score as ks
from yt_niche.core import subniche as sn
from yt_niche.db import runs as run_repo
from yt_niche.services import ai
from yt_niche.services import keywords as kw_svc
from yt_niche.services import report
from yt_niche.services import subniches as sub_svc
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources.trends import compare_trends as compare_trends_source
from yt_niche.sources.trends import describe_errors as describe_trends_errors
from yt_niche.ui import theme
from yt_niche.ui.common import EST_UNITS_PER_DIG, get_conn, handle_youtube_errors, market_label, prompt_box, run_label

conn = get_conn()
theme.inject_css()

# Layer status (core.subniche.layer_status): icon + word, never color alone.
STATUS_ICON = {"alive": "🟢", "weak": "🟡", "too_narrow": "🔴", "video": "🎬"}
STATUS_WORD = {"alive": "Alive", "weak": "Weak", "too_narrow": "Too narrow", "video": "Video topic"}
FILTER_KEY = {v: k for k, v in sn.FILTERS.items()}  # filter label shown in the editor → key stored on the run


def _status_text(status: dict) -> str:
    return f"{STATUS_ICON[status['status']]} {STATUS_WORD[status['status']]}"


# ---------------------------------------------------------------------------
# Header, niche picker, KPIs
# ---------------------------------------------------------------------------

theme.page_header("🌿", "Sub-niches & keywords", "Go one layer down: repeated phrases, search suggestions, keyword scores.")

all_runs = run_repo.list_all(conn)
if not all_runs:
    theme.empty_state("No dig yet.", "Open the Dig page and dig the parent niche first.")
    st.stop()

by_id = {r["id"]: r for r in all_runs}
ids = list(by_id)
current = st.session_state.get("sub_run") or st.session_state.get("current_run")
run_id = st.selectbox(
    "Niche to work on (the parent when digging deeper)",
    ids,
    index=ids.index(current) if current in ids else 0,
    format_func=lambda i: f"{'· ' * ((by_id[i].get('layer') or 1) - 1)}Layer {by_id[i].get('layer') or 1} · {run_label(by_id[i])}",
)
st.session_state["sub_run"] = run_id
run = run_repo.get(conn, run_id)
r = run["result"]
language = run["language"]
layer = run.get("layer") or 1
status = sn.layer_status(run)
scored_here = kw_svc.scored_for(conn, run["topic"], run["region"])
good_here = [k for k in scored_here if k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]]

theme.kpi_row([
    ("Layer", layer, f"niche up to layer {cfg.SUBNICHE_MAX_LAYER}"),
    ("Layer status", _status_text(status), None),
    ("Outperformers", r["outlier_counts"]["total"], None),
    ("Small booming channels", len(r["small_boom_channels"]), None),
    (f"Keywords ≥ {cfg.KEYWORD_GOOD}", f"{len(good_here)}/10", "long-tail, scored"),
])
st.caption(status["text"])

tab_sub, tab_tree, tab_kw, tab_prompt = st.tabs(["1. Sub-niches", "2. Tree & viability", "3. Keywords", "4. Prompts"])

# ---------------------------------------------------------------------------
# 1. Sub-niches: repeated phrases in winning titles + search suggestions → dig deeper
# ---------------------------------------------------------------------------

with tab_sub:
    phrases = sn.repeated_phrases(r["outliers"], run["topic"])
    theme.section(
        "Repeated phrases in outperformer titles",
        "Evidence that viewers like a sub-topic: the phrase shows up in winning titles from several different channels.",
    )
    if phrases:
        st.dataframe(
            pd.DataFrame([{"Phrase": p["phrase"], "Outperformers": p["videos"], "Channels": p["channels"]} for p in phrases]),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(f"No phrase repeats across {cfg.SUBNICHE_MIN_CHANNELS} or more channels yet.")

    theme.section(
        "Search suggestions",
        "Free, no API quota. Suggestions are sorted into sub-niche candidates, long-tail keywords and short keywords.",
    )
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        az = c2.checkbox("Expand A–Z (more variants, about 25 suggestion calls)", key="sub_az")
        if c1.button("🔎 Fetch YouTube suggestions", help="Free, no API quota", width="stretch"):
            with st.spinner("Fetching suggestions…"):
                try:
                    got = kw_svc.fetch_suggestions(conn, run["topic"], run["region"], az=az)
                    st.success(f"Fetched {len(got)} suggestions.")
                except Exception as e:  # network error, temporary block by Google…
                    st.error(f"Could not fetch suggestions: {e}")

    saved = kw_svc.list_keywords(conn, run["topic"], run["region"])
    cols = sn.columns(saved, run["topic"], language, r["outliers"])
    if saved:
        st.caption(f"{len(saved)} suggestions for \"{run['topic']}\" in {market_label(run['region'])}.")
        col_sub, col_long, col_short = st.columns(3)
        for col, kind in ((col_sub, sn.SUBNICHE), (col_long, sn.LONG), (col_short, sn.SHORT)):
            col.markdown(f"**{kind}** {theme.pill(str(len(cols[kind])))}", unsafe_allow_html=True)
            rows = [
                {"Keyword": x["keyword"], "Outperformers": x["videos"]} if kind == sn.SUBNICHE else {"Keyword": x["keyword"]}
                for x in cols[kind]
            ]
            if rows:
                col.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300)
            else:
                col.caption("—")
        with st.expander("How to read the three columns"):
            st.markdown(
                f"- **{sn.SUBNICHE}**: candidates for the next layer; prefer phrases that already have outperformers.\n"
                f"- **{sn.LONG}**: score them in tab 3; the first 10 videos target long-tail keywords scoring {cfg.KEYWORD_GOOD} or more.\n"
                f"- **{sn.SHORT}**: high search volume, high competition; use as secondary keywords in descriptions."
            )

    theme.section(
        "Dig deeper: up to 3 sub-niches",
        "Add exactly one filter per layer, then dig again. Narrow, but there must still be viewers.",
    )
    with st.expander("The four filters, one per layer"):
        st.dataframe(
            pd.DataFrame([{"Filter": sn.FILTERS[k], "Example": sn.FILTER_EXAMPLES[k]} for k in sn.FILTERS]),
            hide_index=True,
            width="stretch",
        )
    with st.container(border=True):
        if layer >= cfg.SUBNICHE_MAX_LAYER:
            st.warning(
                f"This niche is already at layer {layer}. Layer {cfg.SUBNICHE_MAX_LAYER + 1} and below is usually a video topic, "
                "not a niche: use it to plan videos, do not build a whole channel on it."
            )
        if status["status"] == "too_narrow":
            st.error("This niche is already too narrow. Do not go one layer down; pick its parent and try a different filter.")
        candidates = [x["keyword"] for x in cols[sn.SUBNICHE][:3]]
        editor = st.data_editor(
            pd.DataFrame(
                {
                    "Dig": [True] * len(candidates) + [False] * (3 - len(candidates)),
                    "Sub-niche": candidates + [""] * (3 - len(candidates)),
                    "Filter to add": [sn.FILTERS["problem"]] * 3,
                }
            ),
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Filter to add": st.column_config.SelectboxColumn(
                    options=list(sn.FILTERS.values()), required=True,
                    help="One filter per layer: " + "; ".join(f"{sn.FILTERS[k]}: {v}" for k, v in sn.FILTER_EXAMPLES.items()),
                ),
            },
            key=f"deep_{run_id}",
        )
        todo = [row for _, row in editor.iterrows() if row["Dig"] and str(row["Sub-niche"]).strip()]
        st.caption(
            "Same market, time window and format as the niche above. "
            f"About {EST_UNITS_PER_DIG} units per sub-niche → {len(todo) * EST_UNITS_PER_DIG} units."
        )
        if st.button(f"⛏️ Dig {len(todo)} sub-niches", type="primary", disabled=not todo or not cfg.api_key()):
            params = r["params"]
            for row in todo:
                topic = " ".join(str(row["Sub-niche"]).split())
                with st.spinner(f"Digging \"{topic}\"…"):
                    new_id = handle_youtube_errors(
                        dig,
                        conn,
                        DigParams(**{**params, "topic": topic}),
                        category=run["category"],
                        affiliates=run["affiliates"],
                        parent_id=run_id,
                        filter_type=FILTER_KEY[row["Filter to add"]],
                    )
                if new_id is None:
                    break
            st.rerun()

# ---------------------------------------------------------------------------
# 2. Niche tree and the viability checklist
# ---------------------------------------------------------------------------

with tab_tree:
    root = sub_svc.root_of(conn, run_id)
    nodes = sub_svc.tree(conn, root)
    theme.section("Niche tree", "The parent niche and every sub-niche dug from it. Status says whether a layer is still alive.")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Niche": "　" * n["depth"] + ("└ " if n["depth"] else "") + n["run"]["topic"],
                    "Layer": n["run"].get("layer") or 1,
                    "Filter": sn.FILTERS.get(n["run"].get("filter_type"), ""),
                    "Score": round(n["run"]["score"]),
                    "Confidence": n["run"]["confidence"],
                    "Outperformers": n["run"]["result"]["outlier_counts"]["total"],
                    "Small booming": len(n["run"]["result"]["small_boom_channels"]),
                    "Status": _status_text(n["status"]),
                    "#": n["run"]["id"],
                }
                for n in nodes
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
    )
    if len(nodes) == 1:
        st.caption("No sub-niche yet. Dig deeper in tab 1.")
    else:
        with st.expander("📈 Compare Google Trends: parent vs up to 4 sub-niches (same scale)"):
            root_run = nodes[0]["run"]
            kids = list(dict.fromkeys(n["run"]["topic"] for n in nodes[1:] if n["run"]["topic"] != root_run["topic"]))
            chosen = st.multiselect("Sub-niches", kids, default=kids[:4], max_selections=4, key=f"trcmp_{root}")
            if st.button("Compare Trends", disabled=not chosen, key=f"trcmp_btn_{root}"):
                with st.spinner("Fetching Google Trends…"):
                    got = asyncio.run(
                        compare_trends_source(conn, [root_run["topic"], *chosen], root_run["region"])
                    )
                st.session_state[f"trcmp_{root}_res"] = got
            got = st.session_state.get(f"trcmp_{root}_res")
            if got:
                if got["errors"]:
                    st.warning(describe_trends_errors(got["errors"]))
                rows = sn.compare_trends(got["series"], root_run["topic"])
                if rows:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Topic": ("🌳 " if x["is_parent"] else "") + x["topic"],
                                    "12 months": x["direction_12m"],
                                    "5 years": x["direction_5y"],
                                    "Level, last 13 weeks": x["level"],
                                    "Good candidate": "⭐" if x["good_candidate"] else "",
                                }
                                for x in rows
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )
                    points = (got["series"].get("12m") or {})
                    if any(points.values()):
                        frames = {t: pd.Series({d: v for d, v in pts}) for t, pts in points.items() if pts}
                        st.line_chart(pd.DataFrame(frames), height=240)
                    st.caption("A sub-niche that rises while the parent is flat or falling is a good candidate (⭐).")

    theme.section(f"Viability check: {run['topic']}", "Five checks. The sub-niche is viable when all five pass.")
    check = sub_svc.check(conn, run_id)
    if ai.enabled(conn, "prompts"):
        if st.button(
            "🤖 Let AI write the promise and 30 ideas", key=f"ai_fill_{run_id}",
            help="Based on the dig result, scored long-tail keywords and outperformer videos. A promise you already wrote is kept; "
                 "the ideas box is replaced by the AI result. Costs one AI call.",
        ):
            with st.spinner("AI is writing the promise and ideas…"):
                try:
                    prompt = report.build_ideas_prompt(
                        run, check["promise"], kw_svc.scored_for(conn, run["topic"], run["region"]),
                        st.session_state.get(f"sub_style_{run_id}", ""),
                    )
                    got = ai.fill_viability(conn, prompt, language, check["promise"])
                except ai.AIError as e:
                    st.error(str(e))
                else:
                    sub_svc.save_check(conn, run_id, ideas="\n".join(got["ideas"]), promise=got["promise"])
                    st.rerun()
    else:
        st.caption("Turn on AI (Settings → AI, prompts feature) to have the channel promise and 30 ideas written for you.")
    with st.form(f"check_{run_id}"):
        promise = st.text_input(
            "Channel promise (one sentence)",
            value=check["promise"],
            placeholder="This channel helps [who] understand / do [what]",
        )
        ideas = st.text_area(
            f"{cfg.SUBNICHE_MIN_IDEAS} video ideas (one per line; you can paste the output of the \"Check 30 ideas\" prompt)",
            value=check["ideas"],
            height=220,
        )
        if st.form_submit_button("Save and check", type="primary"):
            sub_svc.save_check(conn, run_id, ideas=ideas, promise=promise)
            st.rerun()

    for c in check["checks"]:
        icon = "✅" if c["ok"] else "⬜" if c["ok"] is None else "❌"
        kind = "green" if c["ok"] else "" if c["ok"] is None else "red"
        st.markdown(f"{icon} **{c['label']}** {theme.pill(c['detail'], kind)}", unsafe_allow_html=True)
    dups = sn.duplicate_ideas(sn.parse_ideas(check["ideas"]))
    if dups:
        ideas_list = sn.parse_ideas(check["ideas"])
        with st.expander(f"{len(dups)} duplicate or near-duplicate idea pairs to merge"):
            for i, j in dups[:30]:
                st.markdown(f"- {ideas_list[i]} ↔ {ideas_list[j]}")
    if check["passed"]:
        st.success("✅ Viable. Next: compare it with other niches (Compare page), then weigh it on the Scorecard.")
    elif check["checks"][2]["ok"] is False:
        st.warning(f"Fewer than {cfg.SUBNICHE_MIN_IDEAS} distinct ideas: the niche may be too narrow. Try loosening one filter.")

# ---------------------------------------------------------------------------
# 3. Keywords: suggestions → score → pick → dig
# ---------------------------------------------------------------------------

with tab_kw:
    theme.section(
        "Keyword research",
        "Fetch suggestions for a layer-2 sub-niche, score the long-tail ones, pick the 3–5 best and dig them.",
    )
    with st.container(border=True):
        k1, k2 = st.columns([2, 1])
        seed = k1.text_input("Seed (layer-2 sub-niche)", value=run["topic"], key=f"seed_{run_id}")
        regions = list(dict.fromkeys([run["region"], *cfg.ENABLED_MARKETS]))  # keep the dig's market even if disabled
        region = k2.selectbox("Market", regions, index=0, format_func=market_label, key=f"kwr_{run_id}")
        seed_norm = " ".join(seed.lower().split())

        b1, b2 = st.columns([1, 2])
        az_kw = b2.checkbox("Expand A–Z", key=f"kwaz_{run_id}")
        if b1.button("🔎 Fetch suggestions", key=f"kwget_{run_id}", width="stretch"):
            try:
                kw_svc.fetch_suggestions(conn, seed_norm, region, az=az_kw)
            except Exception as e:
                st.error(f"Could not fetch suggestions: {e}")
        with st.expander("Add keywords by hand"):
            manual = st.text_area("One keyword per line", key=f"kwman_{run_id}")
            if st.button("Add", key=f"kwadd_{run_id}"):
                kw_svc.add_manual(conn, seed_norm, region, manual.splitlines())
                st.rerun()

    kws = kw_svc.list_keywords(conn, seed_norm, region)
    if not kws:
        theme.empty_state("No keywords for this seed yet.", "Click \"Fetch suggestions\" or add keywords by hand.")
    else:
        lang = cfg.MARKETS[region][0]
        good = [k for k in kws if k["score"] is not None and k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]]
        st.progress(
            min(1.0, len(good) / 10),
            text=f"Long-tail keywords scoring ≥ {cfg.KEYWORD_GOOD} without \"{ks.LOW_DATA}\": **{len(good)}/10**",
        )
        df = pd.DataFrame(
            [
                {
                    "Score it": False,
                    "Keyword": k["keyword"],
                    "Type": sn.keyword_kind(k["keyword"], seed_norm, lang),
                    "Score": k["score"],
                    "Label": (ks.label(k["score"]) or "") + (f" · {ks.LOW_DATA}" if k["low_data"] else ""),
                    "Demand": k["demand"],
                    "Competition": k["competition"],
                    "Results": k["n"],
                    "Pick to dig": k["picked"],
                    "Dig #": f"#{k['run_id']}" if k["run_id"] else "",
                }
                for k in kws
            ]
        )
        edited = st.data_editor(
            df,
            hide_index=True,
            width="stretch",
            disabled=["Keyword", "Type", "Score", "Label", "Demand", "Competition", "Results", "Dig #"],
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Demand": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Competition": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Score it": st.column_config.CheckboxColumn(help=f"Tick, then click Score (up to {kw_svc.MAX_SCORE_BATCH} at a time)"),
                "Pick to dig": st.column_config.CheckboxColumn(help="Tick the 3–5 best keywords, save picks, then dig them"),
            },
            key=f"kwed_{run_id}_{region}_{seed_norm}",
        )
        to_score = list(edited.loc[edited["Score it"], "Keyword"])
        if not to_score:  # nothing ticked: score the first unscored long-tail keywords
            to_score = [
                k["keyword"] for k in kws if k["score"] is None and sn.keyword_kind(k["keyword"], seed_norm, lang) == sn.LONG
            ][: kw_svc.MAX_SCORE_BATCH]
        picked = list(edited.loc[edited["Pick to dig"], "Keyword"])

        a1, a2, a3 = st.columns(3)
        if a1.button(
            f"📊 Score {len(to_score)} keywords (≈ {len(to_score) * kw_svc.UNITS_PER_KEYWORD} units)",
            type="primary",
            disabled=not to_score or not cfg.api_key(),
            key=f"kwscore_{run_id}",
            width="stretch",
        ):
            if len(to_score) > kw_svc.MAX_SCORE_BATCH:
                st.error(f"Score at most {kw_svc.MAX_SCORE_BATCH} keywords at a time.")
            else:
                with st.spinner("Scoring keywords on real YouTube results…"):
                    if handle_youtube_errors(kw_svc.score, conn, region, to_score) is not None:
                        st.rerun()
        if a2.button("💾 Save picks", key=f"kwpick_{run_id}", width="stretch"):
            kw_svc.set_picked(conn, region, [k["keyword"] for k in kws], False)
            kw_svc.set_picked(conn, region, picked, True)
            st.rerun()
        dig_list = [k for k in kws if k["picked"] and not k["run_id"]]
        if a3.button(
            f"⛏️ Dig {len(dig_list)} picked (≈ {len(dig_list) * EST_UNITS_PER_DIG} units)",
            disabled=not dig_list or len(dig_list) > kw_svc.MAX_DIG_PICKED or not cfg.api_key(),
            help=f"Save picks first; at most {kw_svc.MAX_DIG_PICKED} keywords",
            key=f"kwdig_{run_id}",
            width="stretch",
        ):
            for k in dig_list:
                with st.spinner(f"Digging \"{k['keyword']}\"…"):
                    params = DigParams(**{**r["params"], "topic": k["keyword"], "region": region, "language": cfg.MARKETS[region][0]})
                    new_id = handle_youtube_errors(dig, conn, params, category=run["category"])
                if new_id is None:
                    break
                kw_svc.link_run(conn, region, k["keyword"], new_id)
            st.rerun()
        if any(k["low_data"] for k in kws if k["score"] is not None):
            st.caption(f"Keywords marked \"{ks.LOW_DATA}\": score them again or verify with a Dig before relying on them.")
        with st.expander("How the keyword score is computed"):
            st.markdown(
                "The score is the app's own assumption: 50% Demand (median views of the search results, 100 views → 0, "
                "100K views → 100) + 50% (100 − Competition). Competition = share of results from channels with "
                f"≥ {cfg.KEYWORD_BIG_CHANNEL_SUBS:,} subs and share of titles that target the exact keyword. "
                f"Thresholds: ≥ {cfg.KEYWORD_VERY_GOOD} very good · {cfg.KEYWORD_GOOD}–{cfg.KEYWORD_VERY_GOOD - 1} good · "
                f"{cfg.KEYWORD_AVERAGE}–{cfg.KEYWORD_GOOD - 1} average."
            )

# ---------------------------------------------------------------------------
# 4. Prompts
# ---------------------------------------------------------------------------

with tab_prompt:
    theme.section("Prompts for Claude / ChatGPT", "Copy a prompt, or run it in the app when AI is on in Settings.")
    style = st.text_input("Intended channel style", placeholder="e.g. faceless, AI voice", key=f"sub_style_{run_id}")
    st.markdown("**Expand sub-niches with the four filters**")
    prompt_box(report.build_subniche_prompt(run, cols, phrases, style))
    st.markdown("**Check 30 ideas for one sub-niche**")
    check = sub_svc.get_check(conn, run_id)
    prompt_box(report.build_ideas_prompt(run, check["promise"], kw_svc.scored_for(conn, run["topic"], run["region"]), style))
