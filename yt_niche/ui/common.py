"""Pieces shared by every page: DB connection, quota sidebar, the "one dig result" block (render_run), prompt box."""

from __future__ import annotations

import hashlib
import threading
import time

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import reading
from yt_niche.core.confidence import LOW
from yt_niche.core.niche_score import LABEL_GREEN, LABEL_POTENTIAL, LABEL_RED, SIZE_BIG, SIZE_HIDDEN, SIZE_MID, SIZE_SMALL, all_channels
from yt_niche.core.three_tier import DROP, TRY, TRY_AFTER_FIX
from yt_niche.db import quota
from yt_niche.db import runs as run_repo
from yt_niche.db.connection import connect
from yt_niche.services import report
from yt_niche.services.runs import reanalyze, refetch_trends
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources import trends as trends_source
from yt_niche.ui.theme import (
    CONFIDENCE_ICON,
    LABEL_ICON,
    TIER_ICON,
    TREND_ICON,
    VERDICT_ICON,
    empty_state,
    inject_css,
    kpi_row,
    label_icon,
    pill,
    pills,
    section,
    tier_icon,
    verdict_icon,
    with_icon,
)

__all__ = [
    "EST_UNITS_PER_DIG", "NO_CATEGORY", "TIER_ICON", "VERDICT_ICON", "LABEL_ICON", "TIER_NAMES",
    "get_conn", "start_radar_thread", "quota_sidebar", "market_label", "fmt_num", "render_run", "prompt_box",
    "run_label", "handle_youtube_errors", "label_pill", "confidence_pill", "verdict_pill", "trend_label",
]

NO_CATEGORY = "(none)"
TIER_NAMES = {"demand": "Demand", "supply": "Supply", "money": "Money"}
# Estimated units for one dig of 2 search pages with no channel cache (measured at the start).
EST_UNITS_PER_DIG = 320
_TIMEFRAME = {"5y": "5 years", "12m": "12 months", "3m": "3 months"}
_LABEL_KIND = {LABEL_GREEN: "green", LABEL_POTENTIAL: "amber", LABEL_RED: "red"}
_VERDICT_KIND = {TRY: "green", TRY_AFTER_FIX: "amber", DROP: "red"}
_CONFIDENCE_KIND = {"high": "green", "medium": "amber", "low": "red"}


@st.cache_resource
def get_conn():
    conn = connect()
    from yt_niche.services.market_catalog import apply as apply_markets

    apply_markets(conn)  # one market list for the whole app (Settings → Markets)
    return conn


@st.cache_resource
def start_radar_thread() -> threading.Thread:
    """Run the Keyword radar in the background while the app is open (checks every minute). Same DB lock as Task Scheduler."""
    from yt_niche.services import radar

    def loop():
        conn = connect()
        while True:
            try:
                radar.tick(conn)
            except Exception:  # one failed pass must not stop the loop
                pass
            time.sleep(60)

    t = threading.Thread(target=loop, name="radar", daemon=True)
    t.start()
    return t


def quota_sidebar() -> int:
    used = quota.used_on(get_conn())
    left = cfg.YOUTUBE_DAILY_QUOTA - used
    with st.sidebar:
        st.caption("YouTube quota today (resets at midnight PT)")
        st.progress(min(1.0, used / cfg.YOUTUBE_DAILY_QUOTA), text=f"{used:,} / {cfg.YOUTUBE_DAILY_QUOTA:,} units")
        st.caption(f"About {max(0, left) // EST_UNITS_PER_DIG} more digs today")
        if not cfg.api_key():
            st.warning("No YouTube API key yet. Add one on the Settings page.")
        try:
            from yt_niche.services.tracking import due_redigs

            due = due_redigs(get_conn())
        except Exception:  # a reminder must never break the sidebar
            due = []
        if due:
            st.info(f"🔔 {len(due)} niches due for the monthly re-dig: see Stage progress.")
        try:
            from yt_niche.services.radar import new_good_count

            good = new_good_count(get_conn())
        except Exception:
            good = 0
        if good:
            st.info(f"📡 {good} new promising keywords in the last 24 hours: see Keyword radar.")
    return left


def market_label(code: str) -> str:
    return f"{cfg.MARKETS[code][1]} ({code})" if code in cfg.MARKETS else code


def fmt_num(x) -> str:
    return report.fmt_num(x)


# ---------------------------------------------------------------------------
# Status pills (icon + word, never color alone). Reusable by any page.
# ---------------------------------------------------------------------------


def label_pill(label: str | None) -> str:
    return pill(with_icon(LABEL_ICON, label), _LABEL_KIND.get(label or "", ""))


def confidence_pill(confidence: str | None) -> str:
    return pill(f"{with_icon(CONFIDENCE_ICON, confidence)} confidence", _CONFIDENCE_KIND.get(confidence or "", ""))


def verdict_pill(verdict: str | None) -> str:
    return pill(with_icon(VERDICT_ICON, verdict), _VERDICT_KIND.get(verdict or "", ""))


def trend_label(trend_class: str | None) -> str:
    """'🌳 evergreen' or 'no Trends data'."""
    return with_icon(TREND_ICON, trend_class) if trend_class else "no Trends data"


# ---------------------------------------------------------------------------
# One dig result
# ---------------------------------------------------------------------------


def render_run(run: dict) -> None:
    inject_css()
    r = run["result"]
    p = r["params"]
    key = f"run{run['id']}"

    st.subheader(f"{p['topic']}")
    st.caption(
        f"{market_label(p['region'])} · {report.FMT_LABEL[p['fmt']]} · {p['window_days']} days · "
        f"dug {run['created_at'][:16].replace('T', ' ')} UTC · dig #{run['id']}"
    )
    pills(label_pill(r["labels"][0]), confidence_pill(r["confidence"]), verdict_pill(r["three_tier"]["verdict"]),
          pill(f"{r['n']} videos"), pill(trend_label(r["trends"]["class"])))
    st.info(r["note"])

    kpi_row([
        ("Niche score", f"{r['score']:.0f}/100", r["labels"][0], "off"),
        ("Demand", f"{r['demand']:.0f}", f"35% · median {fmt_num(r['median_views'])} views", "off"),
        ("Opportunity", f"{r['opportunity']:.0f}", "25% · outperformers from small channels", "off"),
        ("Freshness", f"{r['freshness']:.0f}", "20% · recent videos, views per hour", "off"),
        ("Competition", f"{r['competition']:.0f}", "20% · penalty · share from big channels", "off"),
    ])
    with st.expander("How the score is computed"):
        bars = [
            ("Demand (35%)", r["demand"], f"median {fmt_num(r['median_views'])} views"),
            ("Opportunity (25%)", r["opportunity"], "% of outperformer videos from small channels"),
            ("Freshness (20%)", r["freshness"], "recent videos / views per hour"),
            ("Competition (20%, penalty)", r["competition"], "% of videos from channels with > 1M subs"),
        ]
        for name, value, hint in bars:
            st.progress(value / 100, text=f"{name}: **{value:.0f}** · {hint}")
        st.caption("Score = 0.35 × Demand + 0.25 × Opportunity + 0.20 × Freshness − 0.20 × Competition, clipped to 0–100.")

    _render_reading_notes(r, p)
    _render_seven_cells(run)
    _render_three_tier(run, key)
    _render_money_inputs(run, key)

    raw = run.get("raw") or run_repo.get(get_conn(), run["id"], with_raw=True)["raw"]
    channel_rows = all_channels(raw)
    tab_out, tab_ch, tab_all = st.tabs([
        f"Outperformer videos ({r['outlier_counts']['total']})",
        f"Small booming channels ({len(r['small_boom_channels'])})",
        f"All channels ({len(channel_rows)})",
    ])
    with tab_out:
        _render_outliers(r)
        _render_questions(run, key)
    with tab_ch:
        _render_small_channels(run, key)
    with tab_all:
        _render_all_channels(run, channel_rows, key)

    with st.expander("Long-form vs Shorts"):
        _render_format_split(r)
        _render_format_redig(run, key)
    with st.expander(f"Google Trends · {trend_label(r['trends']['class'])}"):
        _render_trends(run)

    _render_actions(run, key)


def _render_reading_notes(r: dict, p: dict) -> None:
    section("Reading notes", "What the numbers allow you to conclude.")
    advice = reading.confidence_advice(r["confidence"])
    if r["confidence"] == LOW:
        st.warning(advice)
    else:
        st.markdown(f"- {advice}")
    st.markdown(f"- {with_icon(LABEL_ICON, r['labels'][0])}: {reading.zone_advice(r['score'])}")
    if r["n_estimated"]:
        st.markdown(f"- {r['n_estimated']} estimated numbers (≈). {reading.ESTIMATE_NOTE}")
    if warn := reading.topic_warning(p["topic"], p["language"]):
        st.markdown(f"- ⚠️ {warn}")


def _render_seven_cells(run: dict) -> None:
    sheet = reading.seven_cells(run)
    title = "✅ Seven-cell sheet: complete" if sheet["complete"] else f"📝 Seven-cell sheet: missing {', '.join(sheet['missing'])}"
    with st.expander(title, expanded=False):
        for c in sheet["cells"]:
            st.markdown(f"{'✅' if c['ok'] else '⬜'} **{c['label']}**: {c['value']}")
        st.caption("Only a complete sheet (medium or high confidence, with Trends) goes into the Compare page.")


def _render_three_tier(run: dict, key: str) -> None:
    t = run["result"]["three_tier"]
    section("Three tiers: Demand × Supply × Money", "One weak tier and the niche is weak.")
    cols = st.columns(3)
    for col, tier in zip(cols, ("demand", "supply", "money")):
        level = t[tier]["level"]
        with col, st.container(border=True):
            st.markdown(f"{tier_icon(level)} **{TIER_NAMES[tier]}** · {level}")
            st.caption(t[tier]["reason"])
    for fix in t["fixes"]:
        st.markdown(f"- 🛠️ {fix}")


def _render_money_inputs(run: dict, key: str) -> None:
    r = run["result"]
    with st.expander("Money: topic category (RPM) and affiliate products", expanded=not run["affiliates"]):
        cats = [NO_CATEGORY] + rpm_source.categories()
        current = run["category"] if run["category"] in cats else NO_CATEGORY
        category = st.selectbox("Topic category (for the RPM lookup)", cats, index=cats.index(current), key=f"{key}_cat")
        affiliates_text = st.text_area(
            "Affiliate products that fit naturally (one per line)",
            value="\n".join(run["affiliates"]),
            key=f"{key}_aff",
            placeholder="History books\nReprinted old maps\nHistory comics",
        )
        if r["rpm"]:
            income = r["income_per_video"]
            st.caption(
                f"RPM ≈ {r['rpm'][0]:g}–{r['rpm'][1]:g} USD ({r['rpm_tier']} tier) → income per video ≈ "
                f"{income[0]:g}–{income[1]:g} USD. An estimate for comparing niches, not real income."
            )
        st.caption(reading.MONETIZATION)
        st.caption("To see the estimated income change by market: 🌍 Compare → Multi-market.")
        if st.button("Recalculate (no quota)", key=f"{key}_reanalyze"):
            affiliates = [line.strip() for line in affiliates_text.splitlines() if line.strip()]
            reanalyze(get_conn(), run["id"], category=None if category == NO_CATEGORY else category, affiliates=affiliates)
            st.rerun()


def _render_outliers(r: dict) -> None:
    if not r["outliers"]:
        empty_state("No video reached 3× its channel median.")
        return
    df = pd.DataFrame(
        [
            {
                "×": round(v["outlier"], 1),
                "Level": v["level"],
                "Title": v["title"],
                "Views": v["views"],
                "Channel median": v["channel_median"],
                "Channel subs": v["subs"],
                "Format": report.FMT_LABEL.get(v["fmt"], v["fmt"]),
                "Published": v["published_at"][:10],
                "Open": f"https://www.youtube.com/watch?v={v['video_id']}",
            }
            for v in r["outliers"]
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "×": st.column_config.NumberColumn(format="%.1f×"),
            "Open": st.column_config.LinkColumn(display_text="▶"),
            "Views": st.column_config.NumberColumn(format="localized"),
            "Channel median": st.column_config.NumberColumn(format="localized"),
            "Channel subs": st.column_config.NumberColumn(format="localized"),
        },
    )


def _render_questions(run: dict, key: str) -> None:
    from yt_niche.services.comments import MAX_VIDEOS, questions_for_run

    if not run["result"]["outliers"]:
        return
    n = min(MAX_VIDEOS, len(run["result"]["outliers"]))
    state_key = f"{key}_questions"
    if st.button(
        f"❓ Question comments on the top {n} outperformers (≈ {n} units)",
        key=f"{key}_qbtn",
        help="Unanswered questions are unmet demand and a source of video ideas (lesson 2.1).",
        disabled=not cfg.api_key(),
    ):
        with st.spinner("Reading comments…"):
            got = handle_youtube_errors(questions_for_run, get_conn(), run)
        if got is not None:
            st.session_state[state_key] = got
    got = st.session_state.get(state_key)
    if got:
        st.caption(f"{len(got['questions'])} questions in {got['n_comments']} comments across {got['n_videos']} videos.")
        if got["questions"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Question": q["question"][:300], "Likes": q["likes"], "Replies": q["replies"], "Video": q["video_title"]}
                        for q in got["questions"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _render_all_channels(run: dict, rows: list[dict], key: str) -> None:
    if not rows:
        st.caption("No channels.")
        return
    st.caption(
        "Every channel with a video in this dig's search results (from saved data, no quota). "
        f"Small < {fmt_num(cfg.SMALL_CHANNEL_MAX_SUBS)} subs, big ≥ {fmt_num(cfg.BIG_CHANNEL_MIN_SUBS)} subs. "
        "The count depends on the sample: each search page is 50 videos (100 units); pick 2 pages in the Dig form for a bigger sample."
    )
    sizes = [SIZE_SMALL, SIZE_MID, SIZE_BIG, SIZE_HIDDEN]
    counts = {s: sum(r["size"] == s for r in rows) for s in sizes}
    picked = st.multiselect(
        "Channel size", sizes, default=sizes, key=f"{key}_allch_size",
        format_func=lambda s: f"{s} ({counts[s]})",
    )
    shown = [r for r in rows if r["size"] in picked]
    df = pd.DataFrame(
        [
            {
                "Channel": r["title"],
                "Link": f"https://www.youtube.com/channel/{r['channel_id']}",
                "Subs": r["subs"],
                "Size": r["size"],
                "Age (months)": r["age_months"],
                "Channel videos": r["video_count"],
                "Videos in sample": r["n_videos"],
                "Sample views": r["total_views"],
                "Outperformers": r["n_outliers"],
                "Best ×": r["best_outlier"],
                "Top video": r["best_title"],
                "Views": r["best_views"],
                "Video link": f"https://www.youtube.com/watch?v={r['best_video_id']}",
            }
            for r in shown
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Link": st.column_config.LinkColumn(display_text="Open channel"),
            "Video link": st.column_config.LinkColumn(display_text="Watch"),
            "Subs": st.column_config.NumberColumn(format="localized"),
            "Sample views": st.column_config.NumberColumn(format="localized"),
            "Views": st.column_config.NumberColumn(format="localized"),
            "Best ×": st.column_config.NumberColumn(format="%.1f×"),
        },
    )
    st.download_button(
        "⬇️ Download CSV", df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"channels_{run['id']}.csv", mime="text/csv", key=f"{key}_allch_csv",
    )


def _render_small_channels(run: dict, key: str) -> None:
    cards = run["result"]["small_boom_channels"]
    if run["result"]["suggest_sub_niche"]:
        st.warning("Fewer than 3 small booming channels: dig a sub-niche (add an audience, problem or angle filter).")
    if not cards:
        return
    checked = set(run["checked_channels"])
    notes = run.get("channel_notes") or {}
    card_ids = {c["channel_id"] for c in cards}
    opened = (checked | {cid for cid, n in notes.items() if n.get("reup")}) & card_ids
    with_notes = {cid for cid, n in notes.items() if n.get("note")} & card_ids
    st.caption(
        "Open at least 3 channels to see whether they make their own content or re-up, and note what repeats "
        f"(angle, length, thumbnail style). Opened: **{len(opened)}/3** · with notes: **{len(with_notes)}/3**. "
        "A re-up channel is not a model to learn from, however big its numbers."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Channel": c["title"],
                    "Link": f"https://www.youtube.com/channel/{c['channel_id']}",
                    "Subs": c["subs"],
                    "Age (months)": c["age_months"],
                    "Videos": c["video_count"],
                    "Young": "🌱" if c["young"] else "",
                    "Top video": c["best_title"],
                    "Views": c["best_views"],
                    "×": c["best_outlier"],
                    "Video link": f"https://www.youtube.com/watch?v={c['best_video_id']}",
                    "Opened": "✅" if c["channel_id"] in checked else "",
                    "Re-up": "♻️" if notes.get(c["channel_id"], {}).get("reup") else "",
                    "Note": notes.get(c["channel_id"], {}).get("note", ""),
                }
                for c in cards
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Link": st.column_config.LinkColumn(display_text="Open channel"),
            "Video link": st.column_config.LinkColumn(display_text="Watch"),
            "Subs": st.column_config.NumberColumn(format="localized"),
            "Views": st.column_config.NumberColumn(format="localized"),
            "×": st.column_config.NumberColumn(format="%.1f×"),
        },
    )

    def _save_note(channel_id: str) -> None:
        run_repo.set_channel_note(
            get_conn(),
            run["id"],
            channel_id,
            note=st.session_state[f"{key}_note_{channel_id}"],
            reup=st.session_state[f"{key}_reup_{channel_id}"],
        )

    def _toggle(channel_id: str, widget_key: str) -> None:
        current = set(run_repo.get(get_conn(), run["id"])["checked_channels"])
        current.add(channel_id) if st.session_state[widget_key] else current.discard(channel_id)
        run_repo.set_checked_channels(get_conn(), run["id"], sorted(current))

    by_id = {c["channel_id"]: c for c in cards}
    with st.container(border=True):
        st.markdown("**Review a channel**")
        cid = st.selectbox(
            "Channel", list(by_id), format_func=lambda i: by_id[i]["title"], key=f"{key}_review_pick",
            label_visibility="collapsed",
        )
        c = by_id[cid]
        st.caption(
            f"[{c['title']}](https://www.youtube.com/channel/{cid}) · {fmt_num(c['subs'])} subs · {c['age_months']} months · "
            f"{c['video_count']} videos{' · 🌱 young channel' if c['young'] else ''} · "
            f"[{c['best_title'][:80]}](https://www.youtube.com/watch?v={c['best_video_id']}) "
            f"{fmt_num(c['best_views'])} views, **{c['best_outlier']:g}×**"
        )
        wkey = f"{key}_chk_{cid}"
        st.checkbox(
            "Opened it, the channel makes its own content",
            value=cid in checked,
            key=wkey,
            on_change=_toggle,
            args=(cid, wkey),
        )
        n = notes.get(cid, {})
        st.checkbox(
            "Re-up channel / reuses other people's content",
            value=bool(n.get("reup")),
            key=f"{key}_reup_{cid}",
            on_change=_save_note,
            args=(cid,),
        )
        st.text_input(
            "What repeats",
            value=n.get("note", ""),
            placeholder="angle, length, thumbnail style…",
            key=f"{key}_note_{cid}",
            on_change=_save_note,
            args=(cid,),
        )


def _render_format_split(r: dict) -> None:
    fs = r.get("format_split")
    if not fs:
        st.caption(f"This dig covers one format only. Choose \"{report.FMT_LABEL['any']}\" to compare long-form vs Shorts.")
        return
    c1, c2 = st.columns(2)
    for col, fmt, name in ((c1, "long", "Long-form (≥ 4 min)"), (c2, "shorts", "Shorts (≤ 3 min)")):
        s = fs[fmt]
        with col, st.container(border=True):
            st.markdown(f"**{name}**")
            m1, m2, m3 = st.columns(3)
            m1.metric("Videos", s["n"])
            m2.metric("Median views", fmt_num(s["median_views"]))
            m3.metric("Outperformers", s["outliers"])
    if fs["fit_label"]:
        st.success(fs["fit_label"])
        st.caption(reading.format_advice(fs["fit"]))
    else:
        st.caption("No format wins clearly yet (it has to win on both median views and outperformer count).")
    st.caption("Do not compare Shorts views with long-form views directly; re-dig the format you plan to make for a clean read.")


def _render_format_redig(run: dict, key: str) -> None:
    """Lesson 2.2: once a format is chosen, re-dig it on its own (long-form from 4 min, Shorts under 3 min) and compare scores."""
    from yt_niche.services.fetch import DigParams
    from yt_niche.services.runs import dig

    r = run["result"]
    latest: dict[str, dict] = {}
    for v in run_repo.format_variants(get_conn(), run):
        latest.setdefault(v["fmt"], v)
    if len(latest) > 1:
        st.markdown("**Same topic, score by format** (latest dig of each format)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Format": report.FMT_LABEL[f],
                        "Score": round(v["score"]),
                        "Confidence": with_icon(CONFIDENCE_ICON, v["confidence"]),
                        "Date": v["created_at"][:10],
                        "#": v["id"],
                    }
                    for f, v in latest.items()
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
        )
    fs = r.get("format_split")
    if r["params"]["fmt"] != "any" or not fs or not fs["fit"] or fs["fit"] in latest:
        return
    label = report.FMT_LABEL[fs["fit"]]
    if st.button(
        f"⛏️ Re-dig {label} only to compare scores (≈ {EST_UNITS_PER_DIG} units)", key=f"{key}_fmtdig", disabled=not cfg.api_key()
    ):
        params = DigParams(**{**r["params"], "fmt": fs["fit"]})
        with st.spinner(f"Digging {label} only…"):
            new_id = handle_youtube_errors(
                dig,
                get_conn(),
                params,
                category=run["category"],
                affiliates=run["affiliates"],
                parent_id=run.get("parent_id"),
                filter_type=run.get("filter_type") or "",
            )
        if new_id:
            st.session_state["current_run"] = new_id
            st.rerun()


def _render_trends(run: dict) -> None:
    t = run["result"]["trends"]
    if t["class"]:
        st.markdown(f"**{trend_label(t['class'])}** · {t['reason']}")
        st.caption(t["advice"])
    else:
        st.caption(t["reason"])
    trends_raw = (run.get("raw") or {}).get("trends") or _load_raw_trends(run["id"])
    series = (trends_raw or {}).get("series") or {}
    tabs = st.tabs([_TIMEFRAME[n] for n in ("5y", "12m", "3m")])
    for tab, name in zip(tabs, ("5y", "12m", "3m")):
        with tab:
            points = series.get(name) or []
            if points:
                df = pd.DataFrame(points, columns=["Date", "Interest"]).set_index("Date")
                st.line_chart(df, height=220)
            else:
                st.caption("No data.")
    if derived := [_TIMEFRAME.get(n, n) for n in (trends_raw or {}).get("derived") or []]:
        st.caption(f"The {', '.join(derived)} series are derived from the tail of the weekly 5-year series to save Google calls.")
    if (trends_raw or {}).get("provider") == "dataforseo":
        st.caption("Trends data came through DataForSEO because Google is blocking this machine.")
    if not trends_raw or trends_raw.get("errors"):
        if msg := trends_source.describe_errors((trends_raw or {}).get("errors")):
            st.warning(msg)
        until = trends_source.cooldown_until(get_conn())
        backup = until and dfs.available(get_conn(), "dig")
        label = (
            "🔄 Fetch Trends again via DataForSEO (≈ $0.009)" if backup
            else "🔄 Fetch Google Trends again" + (f" (after {until:%H:%M})" if until else "")
        )
        if st.button(label, key=f"trends_refetch_{run['id']}", disabled=bool(until) and not backup, help="No YouTube quota"):
            with st.spinner("Fetching Google Trends…"):
                got = refetch_trends(get_conn(), run["id"])
            if got["errors"]:
                st.session_state[f"trends_msg_{run['id']}"] = trends_source.describe_errors(got["errors"])
            st.rerun()
        if msg := st.session_state.pop(f"trends_msg_{run['id']}", None):
            st.error(msg)


def _load_raw_trends(run_id: int) -> dict | None:
    run = run_repo.get(get_conn(), run_id, with_raw=True)
    return run["raw"].get("trends") if run else None


def _render_actions(run: dict, key: str) -> None:
    conn = get_conn()
    r = run["result"]
    st.divider()
    c1, c2 = st.columns(2)
    c1.download_button(
        "⬇️ Export Excel",
        data=report.to_excel(run),
        file_name=f"dig_{run['id']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key}_xlsx",
    )
    shortlisted = c2.toggle(
        "Add to scorecard",
        value=bool(run["shortlisted"]),
        disabled=not r["can_shortlist"],
        help="Low confidence digs cannot go on the scorecard." if not r["can_shortlist"] else None,
        key=f"{key}_short",
    )
    if shortlisted != bool(run["shortlisted"]):
        run_repo.set_shortlisted(conn, run["id"], shortlisted)

    with st.expander("📋 Prompt for Claude / ChatGPT"):
        style = st.text_input(
            "Planned channel style", placeholder="e.g. faceless, AI voice, 8–12 minute videos", key=f"{key}_style"
        )
        for name, title in report.PROMPTS.items():
            st.markdown(f"**{title}**")
            prompt_box(report.build_prompt(name, run, style))


def prompt_box(prompt: str) -> None:
    """A prompt to copy into an external AI; with AI enabled in Settings → AI there is also a button to run it in the app."""
    from yt_niche.services import ai

    st.code(prompt, language="markdown")
    conn = get_conn()
    if not ai.enabled(conn, "prompts"):
        return
    answer = ai.cached_answer(conn, prompt)
    if st.button("🔄 Run again with AI" if answer else "🤖 Run with AI", key="aip_" + hashlib.sha1(prompt.encode()).hexdigest()[:16]):
        with st.spinner("AI is answering…"):
            try:
                answer = ai.run_prompt(conn, prompt)
            except ai.AIError as e:
                st.error(str(e))
    if answer:
        with st.container(border=True):
            st.markdown(answer)


def run_label(r: dict) -> str:
    return (
        f"{r['topic']} · {market_label(r['region'])} · {report.FMT_LABEL[r['fmt']]} · {r['window_days']} days · "
        f"{r['created_at'][:10]} · score {r['score']:.0f} (#{r['id']})"
    )


def handle_youtube_errors(fn, *args, **kwargs):
    """Call fn and show a friendly YouTube error; returns None on error."""
    from yt_niche.sources.youtube import QuotaExceeded, YouTubeError

    try:
        return fn(*args, **kwargs)
    except QuotaExceeded:
        st.error("YouTube quota is used up for today. It resets at midnight Pacific time.")
    except YouTubeError as e:
        st.error(f"YouTube API error: {e}")
    return None
