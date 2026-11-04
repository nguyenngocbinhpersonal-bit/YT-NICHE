"""Saved niches page: every dig, re-dig for fresh numbers, compare old vs new."""

import pandas as pd
import streamlit as st

from yt_niche.db import runs as run_repo
from yt_niche.services.report import FMT_LABEL
from yt_niche.services.runs import compare, redig
from yt_niche.ui.common import LABEL_ICON, VERDICT_ICON, get_conn, handle_youtube_errors, market_label, render_run
from yt_niche.ui.theme import CONFIDENCE_ICON, empty_state, inject_css, kpi_row, page_header, section, with_icon

conn = get_conn()
inject_css()
page_header("🗂️", "Saved niches", "Every dig you ran. Re-dig for fresh numbers, compare old vs new.")


def _cell(x, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    if isinstance(x, (int, float)):
        return f"{x:+,.1f}" if signed else f"{x:,.1f}"
    return str(x)


all_runs = run_repo.list_all(conn)
if not all_runs:
    empty_state("No digs yet.", "Open the Dig page and dig your first topic.")
    st.stop()

best = max(all_runs, key=lambda r: r["score"])
n_short = sum(1 for r in all_runs if r["shortlisted"])
kpi_row([
    ("Digs", len(all_runs), f"{len({r['topic'] for r in all_runs})} topics", "off"),
    ("On scorecard", n_short, "ready to rate" if n_short else "none yet", "off"),
    ("Best score", f"{best['score']:.0f}", best["topic"], "off"),
    ("Markets", len({r["region"] for r in all_runs}), " · ".join(sorted({r["region"] for r in all_runs})), "off"),
])

f1, f2 = st.columns([3, 1])
query = f1.text_input("Filter by topic", placeholder="type part of a topic")
with f2:
    st.write("")
    only_short = st.toggle("Only on scorecard")
runs = [
    r
    for r in all_runs
    if query.lower() in r["topic"].lower() and (r["shortlisted"] or not only_short)
]
if not runs:
    empty_state("No dig matches the filter.")
    st.stop()

table = pd.DataFrame(
    [
        {
            "#": r["id"],
            "Topic": r["topic"],
            "Layer": r.get("layer") or 1,
            "Market": market_label(r["region"]),
            "Format": FMT_LABEL[r["fmt"]],
            "Window": f"{r['window_days']} days",
            "Date": r["created_at"][:10],
            "Score": round(r["score"]),
            "Label": with_icon(LABEL_ICON, r["label"]),
            "Confidence": with_icon(CONFIDENCE_ICON, r["confidence"]),
            "Verdict": with_icon(VERDICT_ICON, r["verdict"]),
            "Scorecard": "⭐" if r["shortlisted"] else "",
        }
        for r in runs
    ]
)
event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
)

rows = event.selection.rows
if not rows:
    st.caption("Select a row to see details, re-dig or compare.")
    st.stop()

selected = runs[rows[0]]
st.divider()

with st.container(border=True):
    c1, c2, _ = st.columns([1.4, 1.1, 2.5])
    if c1.button("🔄 Re-dig (fresh numbers)", type="primary", width="stretch",
                 help="Same setup, skips the cache; uses about 200–300 units"):
        with st.spinner("Re-digging…"):
            new_id = handle_youtube_errors(redig, conn, selected["id"])
        if new_id:
            st.success(f"Re-dug as dig #{new_id}. The comparison table is below.")
            st.session_state["compare_pair"] = (selected["id"], new_id)
            st.rerun()

    if c2.button("🗑️ Delete this dig", width="stretch"):
        st.session_state["confirm_delete"] = selected["id"]
    if st.session_state.get("confirm_delete") == selected["id"]:
        st.warning(f"Permanently delete dig #{selected['id']} ({selected['topic']})?")
        d1, d2, _ = st.columns([1, 1, 4])
        if d1.button("Delete", type="primary", width="stretch"):
            run_repo.delete(conn, selected["id"])
            st.session_state.pop("confirm_delete")
            st.rerun()
        if d2.button("Cancel", width="stretch"):
            st.session_state.pop("confirm_delete")
            st.rerun()

# ---- Old vs new with the same setup ----
history = run_repo.same_setup(conn, selected)
if len(history) >= 2:
    with st.container(border=True):
        section(f"Digs with the same setup ({len(history)})", "Pick an old and a new dig; a delta under 5 points is a tie.")
        labels = {h["id"]: f"#{h['id']} · {h['created_at'][:16].replace('T', ' ')} · score {h['score']:.0f}" for h in history}
        ids = list(labels)
        default_old, default_new = st.session_state.get("compare_pair", (ids[1], ids[0]))
        if default_old not in ids or default_new not in ids:
            default_old, default_new = ids[1], ids[0]
        s1, s2 = st.columns(2)
        old_id = s1.selectbox("Old dig", ids, index=ids.index(default_old), format_func=labels.get)
        new_id = s2.selectbox("New dig", ids, index=ids.index(default_new), format_func=labels.get)
        by_id = {h["id"]: h for h in history}
        df = pd.DataFrame(compare(by_id[old_id], by_id[new_id]))
        df.columns = ["Metric", "Old", "New", "Delta"]  # compare() rows are (metric, old, new, delta) in this order
        df["Old"] = df["Old"].map(_cell)
        df["New"] = df["New"].map(_cell)
        df["Delta"] = df["Delta"].map(lambda x: _cell(x, signed=True))
        st.dataframe(df, hide_index=True, width="stretch")
        st.caption("Small booming channels that vanish on re-dig are a signal to consider dropping the niche.")

st.divider()
render_run(run_repo.get(conn, selected["id"], with_raw=True))
