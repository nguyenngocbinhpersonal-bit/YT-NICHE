"""10 test videos page: three-pillar plan, numbers after 7 days, checkpoints at video 5 and 10 (lesson 2.5)."""

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import test_plan as tp
from yt_niche.db import runs as run_repo
from yt_niche.services import scorecards
from yt_niche.services import test_plans as svc
from yt_niche.services.report import build_test_plan_prompt
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn, prompt_box

DECISION_ICON = {tp.KEEP: "✅", tp.ADJUST: "🛠️", tp.SWITCH: "🔀"}
# Drop signals the user ticks by hand (the constant is being renamed in core; accept either name).
DROP_SIGNALS = getattr(tp, "DROP_SIGNALS", None) or getattr(tp, "DROP_FLAGS", {})
C = theme.COLORS

conn = get_conn()
theme.inject_css()
theme.page_header("🎬", "10 test videos", "Plan ten videos, log the numbers, decide at video 5 and 10.")

# ---- Pick a locked scorecard that has a main niche ----
boards = []
for b in scorecards.list_all(conn):
    if b["locked_at"]:
        data = scorecards.load(conn, b["id"])
        if data["decision"] and data["decision"]["main"]:
            boards.append(data)
if not boards:
    theme.empty_state("No scorecard has a main niche yet.", "Go to the ⚖️ Scorecard page first, lock the weights and score every niche.")
    st.stop()

by_id = {b["id"]: b for b in boards}
ids = list(by_id)
current = st.session_state.get("board")
with st.container(border=True):
    board_id = st.selectbox(
        "Scorecard",
        ids,
        index=ids.index(current) if current in ids else 0,
        format_func=lambda i: f"{by_id[i]['name']} v{by_id[i]['version']} → main niche: {by_id[i]['decision']['main']['name']}",
    )
st.session_state["board"] = board_id
board = by_id[board_id]
main = board["decision"]["main"]
backup = board["decision"]["backup"]
main_run = run_repo.get(conn, main["id"])

plan = svc.get(conn, board_id)
videos = plan["videos"] if plan else []
n_published = sum(1 for v in videos if v.get("published_at"))
n_measured = len(tp.measured(videos)) if videos else 0
theme.kpi_row([
    ("Main niche", main["name"], f"{main['final']:g} points"),
    ("Backup niche", backup["name"] if backup else "—", f"{backup['final']:g} points" if backup else "none yet"),
    ("Published", f"{n_published}/{tp.N_VIDEOS}", "videos" if plan else "no plan yet"),
    ("Measured", f"{n_measured}/{tp.N_VIDEOS}", f"checkpoint at {tp.REVIEW_AT} and {tp.DECIDE_AT}"),
])
st.markdown(f"**Channel promise:** {board['promise'] or '_not written yet — add it on the Scorecard page_'}")

# ---------------------------------------------------------------------------
# No plan yet: create one
# ---------------------------------------------------------------------------

if plan is None:
    theme.section("Plan the ten videos", "Each pillar is one content strand; the ten videos rotate through three pillars (4 / 3 / 3). You can change them later.")
    with st.container(border=True):
        per_week = st.number_input("Long-form videos per week", min_value=1, max_value=7, value=2)
        with st.expander("📋 Prompt: suggest 3 pillars, 10 titles, hooks and thumbnails (Claude / ChatGPT)"):
            style = st.text_input("Planned channel style", placeholder="e.g. faceless, AI voice, 8–12 minute videos")
            prompt_box(
                build_test_plan_prompt(
                    board, main_run, per_week=int(per_week), channel_style=style, keywords=svc.keyword_ideas(conn, main_run)
                )
            )
        with st.form("create_plan"):
            cols = st.columns(3)
            pillars = [cols[i].text_input(f"Pillar {i + 1}", key=f"p{i}") for i in range(3)]
            start = st.date_input("First publish date", value=date.today(), format="DD/MM/YYYY")
            st.caption("Have 3–5 videos ready before the first one goes live.")
            if st.form_submit_button("Create plan", type="primary"):
                try:
                    svc.create(conn, board_id, pillars, start, int(per_week))
                    st.rerun()
                except svc.PlanError as e:
                    st.error(str(e))
    st.stop()

# ---------------------------------------------------------------------------
# Plan exists
# ---------------------------------------------------------------------------

if plan["run_id"] != main["id"]:
    st.warning("The scorecard's main niche changed after this plan was created.")

for issue in tp.plan_issues(plan["pillars"], videos):
    st.warning(issue)

tab_plan, tab_measure, tab_decide, tab_prompt = st.tabs(["📝 Plan", "📊 Numbers after 7 days", "🚦 Milestones", "📋 Prompt"])


def _to_date(s):
    return date.fromisoformat(s) if s else None


with tab_plan:
    theme.section("Plan", "Edit titles, hooks and thumbnail ideas in the table, then save. Keep the main keyword at the start of the title.")
    with st.expander("Rename the three pillars"):
        with st.form("rename"):
            cols = st.columns(3)
            names = [cols[i].text_input(f"Pillar {i + 1}", value=p) for i, p in enumerate(plan["pillars"])]
            if st.form_submit_button("Save pillar names", type="primary"):
                try:
                    svc.rename_pillars(conn, board_id, names)
                    st.rerun()
                except svc.PlanError as e:
                    st.error(str(e))

    df = pd.DataFrame(
        [
            {
                "idx": v["idx"],
                "pillar": v["pillar"],
                "title": v["title"],
                "chars": len(v["title"]),
                "hook": v["hook"],
                "thumbnail": v["thumbnail"],
                "planned_date": _to_date(v["planned_date"]),
                "published_at": _to_date(v["published_at"]),
            }
            for v in videos
        ]
    )
    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["idx", "chars"],
        column_config={
            "idx": st.column_config.NumberColumn("#", width="small"),
            "pillar": st.column_config.SelectboxColumn("Pillar", options=plan["pillars"], required=True),
            "title": st.column_config.TextColumn(f"Title (under {tp.TITLE_MAX} characters, main keyword first)", width="large"),
            "chars": st.column_config.NumberColumn("Chars", width="small"),
            "hook": st.column_config.TextColumn("Hook (first 15 seconds)"),
            "thumbnail": st.column_config.TextColumn("Thumbnail idea"),
            "planned_date": st.column_config.DateColumn("Planned date", format="DD/MM/YYYY"),
            "published_at": st.column_config.DateColumn("Published on", format="DD/MM/YYYY"),
        },
        key=f"plan_editor_{board_id}",
    )
    if st.button("Save plan", type="primary"):
        svc.save_videos(conn, board_id, edited.drop(columns=["chars"]).to_dict("records"))
        st.rerun()

    ideas = svc.keyword_ideas(conn, main_run)
    with st.expander(f"💡 Topics from long-tail keywords ({len(ideas)}/10 scored ≥ 55) and outperformer videos"):
        st.caption("Lesson 2.5: pick ten topics from the long-tail keyword list and the outperformer videos; put the main keyword first in the title.")
        if ideas:
            st.dataframe(
                pd.DataFrame([{"Long-tail keyword": k["keyword"], "Score": round(k["score"]), "Picked": "⭐" if k["picked"] else ""} for k in ideas]),
                hide_index=True,
                width="stretch",
                column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
            )
            if st.button("Fill empty titles with these keywords (draft titles)"):
                n = svc.fill_titles(conn, board_id, [k["keyword"] for k in ideas])
                st.success(f"Filled {n} draft titles. Rewrite them to be compelling, keeping the keyword first.")
                st.rerun()
        else:
            st.caption("No long-tail keyword scored ≥ 55 for the main niche yet: score some on the 🌿 Sub-niches & keywords page.")
        top = main_run["result"]["outliers"][:10]
        if top:
            st.markdown("**Outperformer videos of the main niche** (learn the angle, do not copy)")
            for v in top:
                st.markdown(f"- {v['title']} · {v['outlier']:.1f}×")

with tab_measure:
    theme.section(
        "Numbers after 7 days",
        "Copy the numbers from YouTube Studio 7 days after each video goes live. CTR and retention in %, average view duration in seconds.",
    )
    mdf = pd.DataFrame(
        [
            {
                "idx": v["idx"],
                "title": v["title"],
                "impressions": v["impressions"],
                "ctr": v["ctr"],
                "views": v["views"],
                "avd_seconds": v["avd_seconds"],
                "retention": v["retention"],
                "new_subs": v["new_subs"],
                "asks_more": v["asks_more"],
                "note": v["note"],
            }
            for v in videos
        ]
    )
    medited = st.data_editor(
        mdf,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["idx", "title"],
        column_config={
            "idx": st.column_config.NumberColumn("#", width="small"),
            "title": st.column_config.TextColumn("Title"),
            "impressions": st.column_config.NumberColumn("Impressions", min_value=0, step=1, format="%d"),
            "ctr": st.column_config.NumberColumn("CTR %", min_value=0.0, max_value=100.0, format="%.1f"),
            "views": st.column_config.NumberColumn("Views", min_value=0, step=1, format="%d"),
            "avd_seconds": st.column_config.NumberColumn("Avg view duration (s)", min_value=0, step=1, format="%d"),
            "retention": st.column_config.NumberColumn("Retention %", min_value=0.0, max_value=100.0, format="%.0f"),
            "new_subs": st.column_config.NumberColumn("New subs", step=1, format="%d"),
            "asks_more": st.column_config.CheckboxColumn("Comments ask for more"),
            "note": st.column_config.TextColumn("Notable comments / notes"),
        },
        key=f"measure_editor_{board_id}",
    )
    if st.button("Save numbers", type="primary"):
        svc.save_videos(conn, board_id, medited.drop(columns=["title"]).to_dict("records"))
        st.rerun()

with tab_decide:
    review = tp.review_at_5(videos)
    with st.container(border=True):
        st.markdown(f"#### Video {tp.REVIEW_AT} checkpoint: review")
        st.caption(review["message"])
        for a in review["actions"]:
            st.markdown(f"- {a}")

    with st.container(border=True):
        with st.form("flags"):
            st.markdown("**What you did after the review**")
            fixes = st.checkbox(
                f"Adjusted titles / thumbnails / hooks after the video {tp.REVIEW_AT} checkpoint", value=bool(plan["fixes_applied"])
            )
            out_of_ideas = st.checkbox(f"Ran out of ideas before video {tp.DECIDE_AT}", value=bool(plan["out_of_ideas"]))
            st.markdown("**Drop signals (even if the views are fine)**")
            history = run_repo.same_setup(conn, main_run) if main_run else []
            for hint in tp.auto_drop_hints(history):
                st.caption(f"⚠️ From the re-dig: {hint}")
            drop = [k for k, label in DROP_SIGNALS.items() if st.checkbox(label, value=k in plan["drop_flags"], key=f"drop_{k}")]
            if st.form_submit_button("Save", type="primary"):
                svc.set_flags(conn, board_id, fixes_applied=fixes, out_of_ideas=out_of_ideas, drop_flags=drop)
                st.rerun()

    if plan["drop_flags"]:
        st.error(
            f"{len(plan['drop_flags'])} drop signals ticked. Consider switching to the backup niche"
            + (f" ({backup['name']})." if backup else ".")
        )

    result = tp.decide_at_10(videos, fixes_applied=bool(plan["fixes_applied"]), out_of_ideas=bool(plan["out_of_ideas"]))
    with st.container(border=True):
        st.markdown(f"#### Video {tp.DECIDE_AT} checkpoint: decision")
        if result["decision"]:
            st.markdown(f"## {theme.with_icon(DECISION_ICON, result['decision'])}")
        if result["reasons"]:
            st.markdown("**Why**")
            for r in result["reasons"]:
                st.markdown(f"- {r}")
        if result["actions"]:
            st.markdown("**What to do**")
            for a in result["actions"]:
                st.markdown(f"- 👉 {a}")
        if result["decision"] == tp.SWITCH and backup:
            st.info(f"Backup niche: **{backup['name']}**. Do not run two niches side by side on one channel.")
    with st.expander("How the decision is made"):
        st.markdown(
            f"- **{theme.with_icon(DECISION_ICON, tp.KEEP)}**: at least one video clearly above the median "
            f"(≥ {cfg.TEST_STANDOUT_RATIO:g}× median views) plus a second keep signal (rising average view duration, "
            "or comments asking for more).\n"
            f"- **{theme.with_icon(DECISION_ICON, tp.SWITCH)}**: you ran out of ideas, or after adjusting, impressions are still "
            f"near zero (< {cfg.TEST_IMPRESSIONS_LOW:,}) or no video moved.\n"
            f"- **{theme.with_icon(DECISION_ICON, tp.ADJUST)}**: everything in between — fix titles, thumbnails and hooks, then measure more.\n"
            f"- Nothing is decided before {tp.DECIDE_AT} measured videos."
        )

    diag = result["diag"]
    if diag["n"]:
        theme.kpi_row([
            ("Videos measured", diag["n"]),
            ("Median views", f"{diag['median_views']:,.0f}"),
            ("Median CTR", "—" if diag["median_ctr"] is None else f"{diag['median_ctr']:.1f}%"),
            ("Median retention", "—" if diag["median_retention"] is None else f"{diag['median_retention']:.0f}%"),
        ])
        measured = tp.measured(videos)
        standout_ids = {v["idx"] for v in diag["standouts"]}
        fig = go.Figure(
            go.Bar(
                x=[f"#{v['idx']}" for v in measured],
                y=[v["views"] for v in measured],
                marker_color=[C["blue_pole"] if v["idx"] in standout_ids else C["blue"][5] for v in measured],
                hovertext=[f"{v['title'] or 'video ' + str(v['idx'])}<br>{v['views']:,} views" for v in measured],
                hoverinfo="text",
            )
        )
        if diag["median_views"]:
            fig.add_hline(y=diag["median_views"], line_dash="dot", line_color=C["ink2"],
                          annotation_text="median", annotation_position="top left", annotation_font_color=C["ink2"])
        fig.update_layout(
            height=240, margin=dict(t=10, r=10, l=0, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(title="Video", showgrid=False), yaxis=dict(title="Views after 7 days", gridcolor=C["line"], zeroline=False),
            font=dict(family=theme.FONT, size=theme.CHART_FONT_SIZE),
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        st.caption("Dark bars are the standouts (above the standout ratio of the median).")

with tab_prompt:
    style = st.text_input("Planned channel style", placeholder="e.g. faceless, AI voice, 8–12 minute videos", key="style2")
    prompt_box(build_test_plan_prompt(board, main_run, per_week=plan["per_week"], channel_style=style))

st.divider()
if st.button("🗑️ Delete this plan"):
    st.session_state["confirm_delete_plan"] = board_id
if st.session_state.get("confirm_delete_plan") == board_id:
    st.warning("Delete the plan and every number you entered?")
    d1, d2, _ = st.columns([1, 1, 4])
    if d1.button("Delete", type="primary", width="stretch"):
        svc.delete(conn, board_id)
        st.session_state.pop("confirm_delete_plan")
        st.rerun()
    if d2.button("Cancel", width="stretch"):
        st.session_state.pop("confirm_delete_plan")
        st.rerun()
