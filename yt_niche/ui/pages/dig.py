"""Dig page (lessons 2.1 + 2.2): the dig form and the current result."""

import streamlit as st

from yt_niche import config as cfg
from yt_niche.db import runs as run_repo
from yt_niche.services.fetch import DigParams
from yt_niche.services.report import FMT_LABEL
from yt_niche.services.runs import dig
from yt_niche.sources import rpm as rpm_source
from yt_niche.ui.common import EST_UNITS_PER_DIG, NO_CATEGORY, get_conn, handle_youtube_errors, market_label, render_run
from yt_niche.ui.theme import inject_css, page_header

conn = get_conn()
inject_css()
page_header("", "Dig", "Measure demand × supply × money with real numbers. One weak tier and the niche is weak.")

ORDER_LABEL = {"relevance": "Relevance", "viewCount": "View count"}
TRENDS_SOURCE_LABEL = {"youtube": "YouTube", "web": "Google Web"}

with st.container(border=True), st.form("dig_form"):
    c1, c2 = st.columns([2, 1])
    topic = c1.text_input("Topic (2–4 words)", placeholder="history storytelling")
    markets = cfg.ENABLED_MARKETS
    region = c2.selectbox(
        "Market", markets, index=markets.index("VN") if "VN" in markets else 0, format_func=market_label,
        help="Edit the market list in Settings → Markets",
    )

    c3, c4, c5 = st.columns(3)
    window_days = c3.selectbox("Time window", [7, 30, 90, 365], index=1, format_func=lambda d: f"{d} days")
    fmt = c4.radio("Format", ["any", "long", "shorts"], format_func=FMT_LABEL.get, horizontal=True)
    category = c5.selectbox("Topic category (RPM)", [NO_CATEGORY] + rpm_source.categories())

    with st.expander("Advanced options"):
        a1, a2, a3, a4 = st.columns(4)
        order = a1.selectbox("Sort search results by", list(ORDER_LABEL), format_func=ORDER_LABEL.get)
        pages = a2.selectbox("Search pages", [1, 2], index=1, help="50 videos per page, 100 units each")
        with_trends = a3.checkbox("Include Google Trends", value=True)
        trends_source = a4.selectbox("Trends source", list(TRENDS_SOURCE_LABEL), format_func=TRENDS_SOURCE_LABEL.get)
        fresh = st.checkbox("Skip the cache (fresh numbers, uses quota)", value=False)

    affiliates_text = st.text_area("Affiliate products that fit naturally (optional, one per line)", height=80)
    st.caption(f"A dig uses up to about {EST_UNITS_PER_DIG} units of quota when nothing is cached.")
    submitted = st.form_submit_button("Dig", type="primary")

if submitted:
    if not topic.strip():
        st.error("Enter a topic first.")
    elif not cfg.api_key():
        st.error("No YouTube API key yet. Add one on the Settings page.")
    else:
        params = DigParams(
            topic=topic.strip(),
            region=region,
            language=cfg.MARKETS[region][0],
            window_days=window_days,
            fmt=fmt,
            order=order,
            pages=pages,
            with_trends=with_trends,
            trends_source=trends_source,
        )
        affiliates = [line.strip() for line in affiliates_text.splitlines() if line.strip()]
        with st.spinner(f"Digging… (up to about {EST_UNITS_PER_DIG} units of quota if nothing is cached)"):
            new_id = handle_youtube_errors(
                dig,
                conn,
                params,
                category=None if category == NO_CATEGORY else category,
                affiliates=affiliates,
                fresh=fresh,
            )
        if new_id:
            st.session_state["current_run"] = new_id

run_id = st.session_state.get("current_run")
if run_id and (run := run_repo.get(conn, run_id, with_raw=True)):
    st.divider()
    render_run(run)
