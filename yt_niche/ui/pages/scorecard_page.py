"""Scorecard page: lock the weights → score ten criteria → main niche + backup (lesson 2.5)."""

import pandas as pd
import streamlit as st

from yt_niche.core import scorecard as sc
from yt_niche.db import runs as run_repo
from yt_niche.services import scorecards as svc
from yt_niche.services.report import build_scorecard_review_prompt
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn, prompt_box, run_label

conn = get_conn()
theme.inject_css()
theme.page_header("", "Scorecard", "Ten weighted criteria to lock in a main niche and a backup.")

# ---------------------------------------------------------------------------
# Pick a scorecard / create one
# ---------------------------------------------------------------------------

boards = svc.list_all(conn)
board_ids = [b["id"] for b in boards]
labels = {
    b["id"]: f"{b['name']} · v{b['version']} · {b['n_items']} niches · {'locked' if b['locked_at'] else 'draft'}"
    for b in boards
}
current = st.session_state.get("board")

with st.container(border=True):
    if boards:
        board_id = st.selectbox(
            "Scorecard",
            board_ids,
            index=board_ids.index(current) if current in board_ids else 0,
            format_func=labels.get,
        )
        st.session_state["board"] = board_id
    with st.expander("New scorecard", expanded=not boards):
        candidates = [r for r in run_repo.list_all(conn) if r["result"]["can_shortlist"]]
        if len(candidates) < svc.MIN_ITEMS:
            theme.empty_state(
                f"You need at least {svc.MIN_ITEMS} digs with medium or high confidence.",
                "Go to the Dig page and dig more niches first.",
            )
        else:
            by_id = {r["id"]: r for r in candidates}
            default = [r["id"] for r in candidates if r["shortlisted"]][: svc.MAX_ITEMS]
            with st.form("create_board"):
                name = st.text_input("Name", placeholder="e.g. October niche pick")
                picked = st.multiselect(
                    f"Pick {svc.MIN_ITEMS}–{svc.MAX_ITEMS} niches (digs marked \"Add to scorecard\" are preselected)",
                    list(by_id),
                    default=default,
                    format_func=lambda i: run_label(by_id[i]),
                    max_selections=svc.MAX_ITEMS,
                )
                if st.form_submit_button("Create scorecard", type="primary"):
                    try:
                        st.session_state["board"] = svc.create(conn, name, picked)
                        st.rerun()
                    except svc.ScorecardError as e:
                        st.error(str(e))

if not boards:
    st.stop()

data = svc.load(conn, board_id)
names = {r["id"]: r["topic"] for r in data["runs"]}
weights = data["weights"]

for w in data["warnings"]:
    st.warning(f"{w}. Lesson 2.4: only compare niches dug with the same setup on the same day.")
if data["missing_runs"]:
    st.warning(f"{data['missing_runs']} digs on this scorecard were deleted and are not shown.")
if data["parent_id"]:
    st.caption(f"Version {data['version']}, created from scorecard #{data['parent_id']} (the old version is kept for reference).")

# ---------------------------------------------------------------------------
# Step 1: weights
# ---------------------------------------------------------------------------

theme.section("1. Weights", "Set the weights first, score later. They must add up to 100 and cannot change once locked.")
if not data["locked_at"]:
    with st.expander("How to set the weights"):
        st.markdown(
            "The defaults are a suggestion. A channel that lives on affiliate income can push *Money beyond ads* to 20 "
            "and drop *Ad price* to 0. A criterion with weight 0 is skipped when scoring."
        )
    with st.container(border=True):
        cols = st.columns(5)
        new_weights = {
            c.key: cols[i % 5].number_input(
                c.label, min_value=0, max_value=100, value=int(weights[c.key]), step=5, help=c.guide, key=f"w_{board_id}_{c.key}"
            )
            for i, c in enumerate(sc.CRITERIA)
        }
        total = sum(new_weights.values())
        if total == 100:
            delta, color = "adds up to 100", "off"
        else:
            delta, color = f"{total - 100:+d} (must be 100)", "normal" if total < 100 else "inverse"
        theme.kpi_row([
            ("Weights total", f"{total}/100", delta, color),
            ("Criteria in use", sum(1 for w in new_weights.values() if w > 0), "weight above 0"),
            ("Niches on the card", len(data["runs"]), None),
        ])
        b1, b2, _ = st.columns([1, 1, 3])
        if b1.button("Save weights", width="stretch"):
            try:
                svc.set_weights(conn, board_id, new_weights)
                st.rerun()
            except svc.ScorecardError as e:
                st.error(str(e))
        if b2.button("Lock weights", type="primary", disabled=total != 100, width="stretch"):
            try:
                svc.set_weights(conn, board_id, new_weights)
                svc.lock(conn, board_id)
                st.rerun()
            except svc.ScorecardError as e:
                st.error(str(e))
    st.stop()

with st.container(border=True):
    theme.pills(*[theme.pill(f"{c.label} · {weights[c.key]}", "blue" if weights[c.key] > 0 else "") for c in sc.CRITERIA])
    c1, c2 = st.columns([2.4, 1.2])
    c1.caption(f"Locked at {data['locked_at'][:16].replace('T', ' ')} (UTC).")
    if c2.button("New version to change weights", width="stretch"):
        st.session_state["board"] = svc.new_version(conn, board_id)
        st.rerun()

# ---------------------------------------------------------------------------
# Step 2: scoring
# ---------------------------------------------------------------------------

theme.section("2. Score each niche 1–5", "Cells the app can compute from the dig are prefilled: confirm or change them, then score the rest by hand.")
_, h2 = st.columns([2.4, 1.2])
if h2.button("Refresh suggestions from new data", width="stretch"):
    svc.refresh_suggestions(conn, board_id)
    st.rerun()

tabs = st.tabs([names[r["id"]] for r in data["runs"]])
for tab, run in zip(tabs, data["runs"]):
    rid = run["id"]
    cells = data["cells"][rid]
    with tab, st.form(f"score_{board_id}_{rid}"):
        st.caption(run_label(run))
        values = {}
        for c in sc.CRITERIA:
            cell = cells[c.key]
            if weights[c.key] == 0:
                continue
            left, mid, right = st.columns([2, 2, 3])
            left.markdown(f"**{c.label}** · weight {weights[c.key]}")
            left.caption(c.guide)
            score = mid.radio(
                c.label,
                [1, 2, 3, 4, 5],
                index=cell["score"] - 1 if cell["score"] else None,
                horizontal=True,
                label_visibility="collapsed",
                key=f"s_{board_id}_{rid}_{c.key}",
            )
            hint = f"Suggested {cell['auto_score']} — {cell['rationale']}" if cell["auto_score"] else cell["rationale"]
            mid.caption(hint)
            note = right.text_input(
                "Reason", value=cell["note"], placeholder="why this score", key=f"n_{board_id}_{rid}_{c.key}"
            )
            values[c.key] = (score, note)
        if st.form_submit_button(f"Save scores: {names[rid]}", type="primary"):
            svc.set_scores(conn, board_id, rid, values)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 3: result
# ---------------------------------------------------------------------------

theme.section("3. Result", "A 1 in any cell is a red flag: that niche cannot be the main niche, however high its total.")
decision = data["decision"]
active = [c for c in sc.CRITERIA if weights[c.key] > 0]
table = pd.DataFrame(
    [
        {
            "Niche": r["name"],
            **{c.label: ("1 (red flag)" if r["scores"].get(c.key) == 1 else r["scores"].get(c.key) or "—") for c in active},
            "Final score": r["final"],
            "Red flags": len(r["red_flags"]) or "",
        }
        for r in decision["ranking"]
    ]
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={"Final score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")},
)
with st.expander("How the final score is computed"):
    st.markdown(
        "Final score = Σ(weight × score 1–5) ÷ 5, so it runs from 20 to 100. Only criteria with weight above 0 count. "
        f"When two niches are within {sc.TIE_GAP} points, the tie is broken by *Sustainable production* + *Money beyond ads*."
    )

if not decision["complete"]:
    theme.empty_state(
        "Score every weighted cell of every niche to see the main and backup niche.",
        "Open each tab above, confirm the suggestions and fill the empty cells.",
    )
    st.stop()

flagged = [r for r in decision["ranking"] if r["red_flags"]]
for r in flagged:
    crits = [theme.pill(f"Red flag: {sc.CRITERIA_BY_KEY[k].label}", "red") for k in r["red_flags"]]
    st.markdown(f"**{r['name']}** ({r['final']:g} points) has a 1 in " + "".join(crits) + " — cannot be the main niche.",
                unsafe_allow_html=True)

main, backup = decision["main"], decision["backup"]
if not main:
    st.error("No niche qualifies as the main niche (every niche has a cell below 2). Dig another niche or a sub-niche.")
    st.stop()

m, b = st.columns(2)
with m.container(border=True):
    st.markdown(f"### Main niche: {main['name']}")
    st.metric("Final score", f"{main['final']:g}/100")
    st.caption(f"Why: {decision['main_reason']}")
with b.container(border=True):
    if backup:
        st.markdown(f"### Backup niche: {backup['name']}")
        st.metric("Final score", f"{backup['final']:g}/100")
        st.caption(f"Why: {decision['backup_reason']}")
    else:
        st.markdown("### Backup niche: none yet")
        st.caption("No other niche qualifies. Dig one more niche close to the main niche.")

with st.container(border=True):
    with st.form(f"promise_{board_id}"):
        promise = st.text_input(
            "Channel promise (one sentence, goes at the top of the channel description)",
            value=data["promise"],
            placeholder="e.g. Understand why you spend the way you do, and change one habit a week.",
        )
        close = st.checkbox(
            "The backup niche shares the main niche's audience and visual style",
            value=bool(data["backup_close"]),
            help="So a switch keeps the audience and does not force a new channel identity.",
        )
        if st.form_submit_button("Save", type="primary"):
            svc.set_promise(conn, board_id, promise, close)
            st.rerun()

if not data["promise"]:
    st.warning("No channel promise yet.")
if backup and not data["backup_close"]:
    st.warning("The backup should share the main niche's audience. If it does not, consider another backup.")
st.caption("Do not run the main and backup niche side by side on one channel; the backup is only for switching.")

with st.expander("Prompt: challenge this scorecard (Claude / ChatGPT)"):
    prompt_box(build_scorecard_review_prompt(data))
st.info("Next: open 10 test videos to plan the main niche.")

st.divider()
if st.button("Delete this scorecard"):
    st.session_state["confirm_delete_board"] = board_id
if st.session_state.get("confirm_delete_board") == board_id:
    st.warning("Delete this scorecard for good? The digs stay.")
    d1, d2, _ = st.columns([1, 1, 4])
    if d1.button("Delete", type="primary", width="stretch"):
        svc.delete(conn, board_id)
        st.session_state.pop("confirm_delete_board")
        st.session_state.pop("board", None)
        st.rerun()
    if d2.button("Cancel", width="stretch"):
        st.session_state.pop("confirm_delete_board")
        st.rerun()
