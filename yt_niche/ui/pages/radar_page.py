"""Keyword radar page: Google Trends categories scanned on a schedule, keywords filtered down to the ones worth a dig."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import radar as rd
from yt_niche.services import ai, radar
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import trends
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn, handle_youtube_errors, market_label

C = theme.COLORS

conn = get_conn()
theme.inject_css()
theme.page_header("📡", "Keyword radar", "Google Trends categories scanned on a schedule; keywords that pass are worth a dig.")
with st.expander("How the radar works"):
    st.markdown(
        "Seeds come from Google Trends' official category tree (top and rising queries per category and market), minus news, "
        "celebrity and event categories. Every new keyword passes three filters: Google's entity type plus AI (person, song, "
        "show, team, event → excluded), 30-day search volume on YouTube Search and Google Search (almost nobody searching on "
        "either → excluded), then the YouTube keyword score. Scans are spread over 24 hours so Google does not block (429)."
    )
# AI columns appear once at least one keyword was classified by AI (turn it on in Settings → AI).
show_ai = conn.execute("SELECT 1 FROM radar_keyword WHERE ai_type IS NOT NULL LIMIT 1").fetchone() is not None

try:
    tree = trends.category_tree(conn)
    cat_names = {c["id"]: c["name"] for c in rd.flatten(tree)}
except trends.Throttled:
    tree, cat_names = None, {}
except Exception as e:  # no network, proxy refused, Google changed the endpoint: keep the rest of the page usable
    tree, cat_names = None, {}
    st.warning(
        f"Could not load the Google Trends category list ({type(e).__name__}). Keyword tables still work; "
        "category names and the Scope tab come back once Google Trends is reachable."
    )

# ---------------------------------------------------------------------------
# KPIs + background scanning
# ---------------------------------------------------------------------------

stats = radar.stats(conn)
counts = stats["counts"]
cooldown = trends.cooldown_until(conn)
theme.kpi_row([
    ("To check", counts.get(rd.PENDING, 0), "new keywords"),
    ("Passed", counts.get(rd.PASSED, 0), f"{counts.get(rd.EXCLUDED, 0)} excluded"),
    ("Watching", counts.get(rd.WATCHING, 0), f"{counts.get(rd.IGNORED, 0)} ignored"),
    ("Next scan", f"{stats['next_due'].astimezone():%H:%M}" if stats["next_due"] else "now", f"every {stats['interval_s']} s"),
    ("Trends cooldown", f"until {cooldown:%H:%M}" if cooldown else "none", "Google blocked (429)" if cooldown else "Google is answering",
     "inverse" if cooldown else "off"),
])

with st.container(border=True):
    theme.section(
        "Background scanning",
        f"Today {stats['jobs_today']}/{stats['jobs_per_day']} Trends scans · one full sweep "
        f"≈ {stats['cycle_days']:g} days · YouTube quota {stats['yt_units_today']}/{cfg.RADAR_YT_UNITS_PER_DAY} units"
        if stats["cycle_days"] is not None else
        f"Today {stats['jobs_today']}/{stats['jobs_per_day']} Trends scans · YouTube quota {stats['yt_units_today']}/{cfg.RADAR_YT_UNITS_PER_DAY} units",
    )
    task = radar.task_status()
    b1, b2, b3 = st.columns([2.4, 1, 1])
    if task is None:
        b1.caption("Scans run only while the app is open. On Windows you can add a scheduled task so they run with the app closed.")
    elif task:
        b1.success("Windows Task Scheduler: **on** — scans run even when the app is closed (the PC must be on).")
        if b2.button("Turn off scheduled task", width="stretch"):
            st.toast(radar.disable_task())
            st.rerun()
    else:
        b1.info("Scans run only while the app is open. Turn on the scheduled task to scan with the app closed.")
        if b2.button("Turn on scheduled task", type="primary", width="stretch"):
            st.toast(radar.enable_task())
            st.rerun()
    if b3.button("▶️ Run one scan now", help="Skips the schedule; still respects the cooldown when Google blocks", width="stretch"):
        with st.spinner("Running one scan…"):
            result = radar.tick(conn, force=True)
        st.toast(f"{result['action']}: {result['detail']}")
        st.rerun()

    if cooldown:
        if why := dfs.unavailable_reason(conn, "radar"):
            st.warning(f"Google Trends is blocking: the radar pauses until {cooldown:%H:%M}, then continues at half speed ({why}).")
        else:
            du = dfs.usage_today(conn)
            st.info(f"Google Trends is blocking until {cooldown:%H:%M}: the radar is using DataForSEO meanwhile "
                    f"(today {du['tasks']} tasks, ${du['cost']:.3f}).")
    with st.expander("Scan pace and how it adapts"):
        st.markdown(
            f"- Current pace: one scan every **{stats['interval_s']} s** ({stats['jobs_per_day']} per day). The app speeds up by "
            f"{cfg.RADAR_SPEEDUP_PCT}% after every {cfg.RADAR_SPEEDUP_EVERY} unblocked scans (fastest {cfg.RADAR_MIN_INTERVAL_S} s) "
            "and halves its speed when Google blocks.\n"
            + (
                f"- Scope: {stats['pairs'] or 0} market × category pairs, ≈ {stats['per_pair']:g} scans per pair (scan + checks of new "
                f"keywords). {stats['slow_pairs']} pairs are scanned sparsely (no data: every {cfg.RADAR_EMPTY_RESCAN_DAYS} days; "
                f"no passing keyword after 3 scans: every {cfg.RADAR_BARREN_RESCAN_DAYS} days). Scanned in the last 24 h: "
                f"{stats['fresh_pairs']}.\n"
            )
            + (
                f"- At the fastest pace ({cfg.RADAR_MIN_INTERVAL_S} s, {stats['max_jobs_per_day']} per day) one sweep takes "
                f"≈ **{stats['cycle_days_at_max']:g} days**; the radar reaches it after ≈ {stats['hours_to_max']:g} h without a block.\n"
                if stats["cycle_days_at_max"] is not None and stats["hours_to_max"] > 0 else
                f"- The radar is already at its fastest pace ({cfg.RADAR_MIN_INTERVAL_S} s per scan).\n"
                if stats["cycle_days_at_max"] is not None else ""
            )
            + (
                f"- {stats['unscanned']} pairs have never been scanned (≈ {stats['first_sweep_days']:g} days at the current pace). "
                "To go faster, remove markets or categories in the Scope tab.\n"
                if stats["unscanned"] else ""
            )
        )

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

KIND = {"rising": "rising", "top": "top"}
# Link to the keyword's 12-month Google Trends chart (YouTube Search) for its market.
LINKS = {
    "Trends YouTube": st.column_config.LinkColumn(display_text="▶️ Open", help="Google Trends, 12 months, YouTube Search, same market"),
}
POTENTIAL_COL = {"Potential": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")}


def _table(rows: list[dict], extra: bool = False) -> pd.DataFrame:
    out = []
    for r in rows:
        yt = r.get("yt") or {}
        out.append(
            {
                "⭐": "⭐" if (r["potential"] or 0) >= cfg.RADAR_GOOD_MIN else "",
                "Keyword": r["keyword"],
                "Trends YouTube": trends.explore_url(r["keyword"], r["region"], "youtube", trends.TIMEFRAMES["12m"]),
                **({"Meaning (AI)": r.get("ai_vi") or "", "Type (AI)": ai.KEYWORD_TYPES.get(r.get("ai_type"), ("",))[0]} if show_ai else {}),
                "Market": market_label(r["region"]),
                "Category": cat_names.get(r["category_id"], r["category_id"]),
                "Source": f"{KIND[r['kind']]} +{r['value']:,}%" if r["kind"] == "rising" else f"{KIND[r['kind']]} ({r['value']})",
                "Days seen": r["seen_days"],
                "YouTube": f"{yt['score']:.0f} · {yt['label']}" + (" · low data" if yt.get("low_data") else "") if yt else "",
                "Potential": r["potential"],
                "Status": rd.STATUS_LABEL[r["status"]],
                "First seen": r["first_seen"][:10],
                **({"Reason": r["reason"], "Entity type": r["entity_type"] or ""} if extra else {}),
            }
        )
    return pd.DataFrame(out)


# One market filter for every result table; only markets enabled in Settings → Markets.
picked = st.multiselect(
    "Markets", cfg.ENABLED_MARKETS, format_func=market_label,
    placeholder=f"All {len(cfg.ENABLED_MARKETS)} enabled markets",
    help="From Settings → Markets; disabled markets are hidden here and the radar stops scanning them.",
)
regions = picked or list(cfg.ENABLED_MARKETS)

tab_good, tab_pending, tab_out, tab_scope, tab_log = st.tabs(
    ["⭐ Passed", "⏳ To check", "🚫 Excluded", "⚙️ Scope", "📜 Log"]
)

with tab_good:
    min_score = st.slider("Potential at least", 0, 100, 0)
    rows = [r for r in radar.keywords(conn, [rd.PASSED, rd.WATCHING], regions) if (r["potential"] or 0) >= min_score]
    if not rows:
        theme.empty_state("No keyword has passed the filters yet.", "The radar needs a few hours of background scanning for its first results.")
    else:
        st.caption("Select rows to watch, ignore, score on YouTube or dig.")
        with st.expander("How potential is computed"):
            st.markdown(
                f"Potential = momentum × {cfg.RADAR_WEIGHTS['momentum']:.0%} + 30-day steadiness × {cfg.RADAR_WEIGHTS['durability']:.0%} "
                f"+ YouTube × {cfg.RADAR_WEIGHTS['youtube']:.0%} + days repeated × {cfg.RADAR_WEIGHTS['persistence']:.0%}. "
                "Without a YouTube score the weight is spread over the other three. The radar scores the highest-potential "
                f"keywords on YouTube by itself within the daily quota budget. ⭐ marks potential ≥ {cfg.RADAR_GOOD_MIN:g}."
            )
        event = st.dataframe(
            _table(rows), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="radar_good",
            column_config={**POTENTIAL_COL, **LINKS},
        )
        chosen = [rows[i] for i in event.selection.rows]
        items = [(r["region"], r["keyword"]) for r in chosen]
        a1, a2, a3, a4 = st.columns(4)
        if a1.button("👁️ Watch", disabled=not chosen, width="stretch"):
            radar.set_status(conn, items, rd.WATCHING)
            st.rerun()
        if a2.button("🙈 Ignore (hide for good)", disabled=not chosen, width="stretch"):
            radar.set_status(conn, items, rd.IGNORED)
            st.rerun()
        if a3.button(f"🔎 Score on YouTube (≈{102 * len(chosen)} units)", disabled=not chosen or len(chosen) > 5 or not cfg.api_key(),
                     width="stretch"):
            with st.spinner("Scoring…"):
                for region, kw in items:
                    if handle_youtube_errors(radar.score_youtube, conn, region, kw) is None:
                        break
            st.rerun()
        if a4.button("⛏️ Dig (≈320 units)", disabled=len(chosen) != 1 or not cfg.api_key(), type="primary", width="stretch"):
            r = chosen[0]
            params = DigParams(topic=r["keyword"], region=r["region"], language=cfg.MARKETS[r["region"]][0])
            with st.spinner(f"Digging \"{r['keyword']}\"…"):
                run_id = handle_youtube_errors(dig, conn, params, category=None)
            if run_id:
                st.session_state["current_run"] = run_id
                st.switch_page("pages/dig.py")

with tab_pending:
    rows = radar.keywords(conn, [rd.PENDING], regions)
    st.caption(f"{len(rows)} new keywords waiting for the entity-type and Trends-shape check (one keyword per scan).")
    if rows:
        st.dataframe(_table(rows), hide_index=True, width="stretch", column_config={**POTENTIAL_COL, **LINKS})
    else:
        theme.empty_state("Nothing waiting to be checked.")

with tab_out:
    rows = radar.keywords(conn, [rd.EXCLUDED, rd.IGNORED], regions)
    st.caption("Excluded keywords with the reason, so you can check the filters are not too strict. They are not scanned again.")
    if rows:
        event = st.dataframe(
            _table(rows, extra=True), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key="radar_out", column_config={**POTENTIAL_COL, **LINKS},
        )
        picked = [(rows[i]["region"], rows[i]["keyword"]) for i in event.selection.rows]
        if st.button("↩️ Send back to the check queue", disabled=not picked):
            radar.set_status(conn, picked, rd.PENDING)
            st.rerun()
    else:
        theme.empty_state("No keyword has been excluded yet.")

with tab_scope:
    if tree is None:
        st.warning("Could not load the Google Trends category tree (Google is blocking). Try again later.")
    else:
        scope = radar.get_scope(conn)
        with st.container(border=True):
            theme.section("Scan scope", "Which markets and categories the radar sweeps. Fewer pairs means a shorter sweep.")
            all_markets = st.checkbox(
                "Scan every market enabled in Settings → Markets (follows that list when you enable or disable one)",
                value=scope["all_markets"],
            )
            markets = st.multiselect(
                "Markets", cfg.ENABLED_MARKETS, default=[m for m in scope["markets"] if m in cfg.ENABLED_MARKETS],
                format_func=market_label, disabled=all_markets,
            )
            options = rd.selectable_categories(tree)
            labels = {c["id"]: ("· " * (c["depth"] - 1)) + c["name"] for c in options}
            cats = st.multiselect(
                "Google Trends categories (root groups and sub-categories)", [c["id"] for c in options],
                default=[c for c in scope["categories"] if c in labels], format_func=lambda i: labels[i],
            )
            st.caption(
                "Always skipped: " + ", ".join(rd.EXCLUDED_CATEGORIES.values()) + ". A root group includes its sub-categories; "
                "Arts & Entertainment is scanned by sub-category by default to avoid celebrity news."
            )
            block = st.text_area("Blocklist (one word per line; queries containing it are skipped)", "\n".join(scope["blocklist"]))
            n_markets = len(cfg.ENABLED_MARKETS) if all_markets else len(markets)
            st.caption(f"{n_markets} markets × {len(cats)} categories = **{n_markets * len(cats)} pairs** per sweep.")
            s1, s2, _ = st.columns([1, 1, 3])
            if s1.button("Save scope", type="primary", width="stretch"):
                radar.save_scope(conn, markets, cats, block.splitlines(), all_markets)
                st.success("Saved.")
                st.rerun()
            if s2.button("Reset to defaults", width="stretch"):
                conn.execute("DELETE FROM radar_meta WHERE key = 'scope'")
                st.rerun()

with tab_log:
    theme.section("📈 Scan pace", "Use this to tune the fastest safe pace.")
    days = st.segmented_control("Window", [1, 3, 7, 30, 180], default=7, format_func=lambda d: f"{d} days", key="pace_days") or 7
    rep = radar.pace_report(conn, days)
    if not rep["rows"]:
        theme.empty_state("No scan called Google in this window.")
    else:
        theme.kpi_row([
            ("Scans that called Google", f"{rep['ticks']:,}", f"{rep['requests']:,} requests · {rep['req_per_tick']:g} per scan"),
            ("Blocked (429)", rep["blocks"], f"{rep['rescued']} rescued with a fresh cookie", "inverse" if rep["blocks"] else "off"),
            ("Real gap (median)", f"{rep['median_gap_s']} s" if rep["median_gap_s"] else "—", "between two scans"),
            ("Max requests/hour unblocked", rep["max_ok_req_1h"], None),
        ])
        if rep["blocks"]:
            st.warning(
                f"Google blocked earliest after **{rep['min_block_req_1h']} requests in 60 minutes**. Staying under 80% of that with "
                f"{rep['req_per_tick']:g} requests per scan, the fastest pace should be ≈ **{rep['suggested_min_interval']} s** "
                f"(currently {cfg.RADAR_MIN_INTERVAL_S} s; change it in Settings → Thresholds → Radar)."
            )
        elif rep["fastest_ok_interval"] is not None:
            st.success(
                f"No block in {days} days; the fastest pace run so far is {rep['fastest_ok_interval']:g} s per scan, "
                f"at most {rep['max_ok_req_1h']} requests per hour. You can lower \"Radar: fastest pace\" step by step "
                "(e.g. −10 s at a time) and watch here."
            )
        df = pd.DataFrame(rep["rows"])
        df["at"] = pd.to_datetime(df["at"]).dt.tz_convert(None) + (pd.Timestamp.now() - pd.Timestamp.now("UTC").tz_localize(None))
        base_layout = dict(
            margin=dict(t=10, r=10, l=0, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"),
            xaxis=dict(showgrid=False), legend=dict(orientation="h", y=1.12),
        )
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df["at"], y=df["interval_after"], name="Set pace (s per scan)", mode="lines",
                                 line=dict(color=C["categorical"][0], width=2)))
        fig.add_trace(go.Scatter(x=df["at"], y=df["gap_s"], name="Real gap (s)", mode="lines",
                                 line=dict(color=C["categorical"][1], width=2)))
        blocked = df[df["throttled"] == 1]
        if not blocked.empty:
            fig.add_trace(go.Scatter(x=blocked["at"], y=blocked["gap_s"], name="Blocked (429)", mode="markers",
                                     marker=dict(color=C["critical"], size=9, symbol="x")))
        fig.update_layout(height=260, yaxis=dict(title="seconds", gridcolor=C["line"], zeroline=False), **base_layout)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        fig2 = go.Figure(go.Bar(x=df["at"], y=df["req_1h"], name="Requests in the last 60 min", marker_color=C["blue"][6]))
        fig2.update_layout(height=200, yaxis=dict(title="requests / 60 min", gridcolor=C["line"], zeroline=False),
                           showlegend=False, **base_layout)
        st.plotly_chart(fig2, width="stretch", config={"displayModeBar": False})
        if rep["blocks"]:
            st.markdown("**Blocks**")
            st.dataframe(
                df[df["throttled"] == 1][["at", "interval_before", "gap_s", "req_1h", "streak"]].rename(columns={
                    "at": "When", "interval_before": "Pace at the time (s)", "gap_s": "Gap to previous scan (s)",
                    "req_1h": "Requests in 60 min", "streak": "Unblocked scans in a row"}),
                hide_index=True, width="stretch",
            )
        st.download_button(
            "⬇️ Download pace log (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"radar_pace_{days}d.csv", mime="text/csv",
        )
    theme.section("📜 Last 50 scans")
    if stats["log"]:
        st.dataframe(
            pd.DataFrame([{"When": e["at"][:19].replace("T", " ") + " UTC", "Action": e["action"], "Detail": e["detail"]} for e in stats["log"]]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("No scans yet.")
    st.caption(f"Errors from background scans (if any) are written to {cfg.DATA_DIR / 'radar_errors.log'}.")
