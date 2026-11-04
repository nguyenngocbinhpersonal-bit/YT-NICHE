"""Stage progress page: the end-of-stage checklist for Pick a niche, and the monthly re-dig reminders."""

from datetime import datetime, timezone

import streamlit as st

from yt_niche import config as cfg
from yt_niche.core.stats import parse_iso
from yt_niche.services import tracking
from yt_niche.services.runs import redig
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn, handle_youtube_errors, market_label

conn = get_conn()
theme.inject_css()

ROLE_KIND = {tracking.MAIN: "green", tracking.BACKUP: "blue", tracking.SHORTLIST: ""}

theme.page_header("", "Stage progress", "Where you are in the Pick-a-niche stage, and what is due.")

# ---------------------------------------------------------------------------
# KPIs: locked scorecard, main niche, backup, days since the last dig
# ---------------------------------------------------------------------------

board = tracking.latest_board(conn)
decision = (board or {}).get("decision") or {}
tracked = tracking.tracked(conn)
due = {t["run"]["id"]: t for t in tracking.due_redigs(conn)}
main_t = next((t for t in tracked if t["role"] == tracking.MAIN), None)
now = datetime.now(timezone.utc)
main_age = (now - parse_iso(main_t["run"]["created_at"])).days if main_t else None

theme.kpi_row([
    ("Scorecard locked", "Yes" if board else "No", board["name"] if board else "lock one on the Scorecard page"),
    ("Main niche", decision["main"]["name"] if decision.get("main") else "—", None),
    ("Backup", decision["backup"]["name"] if decision.get("backup") else "—", None),
    (
        "Days since last dig",
        main_age if main_age is not None else "—",
        ("re-dig due" if main_t and main_t["run"]["id"] in due else f"re-dig every {cfg.REDIG_DAYS} days") if main_t else "main niche",
        "inverse" if main_t and main_t["run"]["id"] in due else "off",
    ),
])

# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------

items = tracking.stage_checklist(conn)
done = sum(i["ok"] for i in items)
theme.section(
    "End-of-stage checklist",
    "Output of the stage: one main niche, one backup, a 10-test-video plan with review points and stop signals.",
)
st.progress(done / len(items), text=f"**{done}/{len(items)}** items done")
with st.container(border=True):
    for i in items:
        st.markdown(theme.check_line(bool(i["ok"]), i["label"], i["detail"]), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Monthly re-dig
# ---------------------------------------------------------------------------

theme.section(
    f"Re-dig every month ({cfg.REDIG_DAYS} days)",
    "Re-dig the main niche (and the backup) with fresh numbers to catch drop signals early: a falling 1-year trend, "
    "small booming channels vanishing. Results show on the 10 test videos page (drop signals).",
)
if not tracked:
    theme.empty_state("Nothing to track yet.", "Add a niche to a scorecard or lock in a main niche first.")
for t in tracked:
    r = t["run"]
    is_due = r["id"] in due
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        flag = theme.pill(f"Due · {due[r['id']]['age_days']} days ago", "red") if is_due else theme.pill("up to date", "green")
        c1.markdown(
            f"**{r['topic']}** {theme.pill(t['role'], ROLE_KIND.get(t['role'], ''))}{flag}<br>"
            f"<span style='color:{theme.COLORS['muted']};font-size:14px'>{market_label(r['region'])} · last dug {r['created_at'][:10]}</span>",
            unsafe_allow_html=True,
        )
        if is_due and c2.button("Re-dig", key=f"redig_{r['id']}", type="primary", disabled=not cfg.api_key(), width="stretch"):
            with st.spinner(f"Re-digging \"{r['topic']}\"…"):
                new_id = handle_youtube_errors(redig, conn, r["id"])
            if new_id:
                st.session_state["current_run"] = new_id
                st.rerun()
