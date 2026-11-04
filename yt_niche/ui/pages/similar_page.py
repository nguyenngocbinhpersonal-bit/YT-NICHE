"""Similar videos & channels page: paste a video or channel URL → related channels and videos (TubeBuddy-style)."""

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.services import similar
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError
from yt_niche.ui import theme
from yt_niche.ui.common import fmt_num, get_conn, market_label

SIGNAL_LABEL = {"related": "Up next", "search": "Video search", "search_channel": "Channel search"}
FMT_SHORT = {"long": "Long-form", "shorts": "Shorts", "mid": "3–4 min"}
KIND_LABEL = {"video": "video", "channel": "channel"}
EST_UNITS = 200  # first lookup for a niche (channels not cached yet); a repeat within 7 days is usually under 20 units

conn = get_conn()
theme.inject_css()

theme.page_header("🔗", "Similar videos & channels", "From one URL: who else serves this audience, and with what.")

# ---------------------------------------------------------------------------
# Lookup form
# ---------------------------------------------------------------------------

with st.form("similar_form"):
    c1, c2 = st.columns([3, 1])
    url = c1.text_input(
        "Video URL or channel URL",
        placeholder="https://www.youtube.com/watch?v=…  or  https://www.youtube.com/@channelname",
    )
    markets = cfg.ENABLED_MARKETS
    region = c2.selectbox(
        "Browse as a viewer in",
        markets,
        index=markets.index("US") if "US" in markets else 0,
        format_func=market_label,
        help="Up next and search are fetched as a viewer in this market. In the US, YouTube auto-translates titles to English, "
        "so channels in other languages can be compared too.",
    )
    a1, a2 = st.columns(2)
    with_source_market = a1.checkbox(
        "Also use the source channel's market", value=True, help="Also fetch Up next in the source channel's country (when declared)"
    )
    fresh = a2.checkbox("Skip cache (fetch fresh numbers)", value=False)
    st.caption(f"Takes 10–30 seconds and up to about {EST_UNITS} units when nothing is cached yet.")
    submitted = st.form_submit_button("Find similar", type="primary")

if submitted:
    if not url.strip():
        st.error("Paste a video URL or channel URL first.")
    elif not cfg.api_key():
        st.error("No YouTube API key yet. Add one on the Settings page.")
    else:
        with st.spinner(f"Searching… (10–30 seconds, up to about {EST_UNITS} units when nothing is cached)"):
            try:
                st.session_state["similar_run"] = similar.find_similar(
                    conn, url.strip(), region, with_source_market=with_source_market, fresh=fresh
                )
            except similar.SimilarError as e:
                st.error(str(e))
            except QuotaExceeded:
                st.error("YouTube quota is used up for today. It resets at midnight Pacific time.")
            except YouTubeError as e:
                st.error(f"YouTube API error: {e}")

runs = similar.list_runs(conn)
if not runs:
    theme.empty_state("No lookup yet.", "Paste a video or channel URL above and click Find similar.")
    st.stop()

# ---------------------------------------------------------------------------
# Past lookups
# ---------------------------------------------------------------------------

theme.section("Past lookups", "Pick a lookup to see its result. Repeating a lookup within 7 days mostly hits the cache.")
ids = [r["id"] for r in runs]
current = st.session_state.get("similar_run")
labels = {
    r["id"]: f"{r['label']} · {KIND_LABEL.get(r['kind'], r['kind'])} · {market_label(r['region'])} · "
    f"{r['created_at'][:10]} (#{r['id']})"
    for r in runs
}
run_id = st.selectbox(
    "Lookup", ids, index=ids.index(current) if current in ids else 0, format_func=labels.get, key="similar_pick"
)
with st.expander(f"All past lookups ({len(runs)})"):
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "#": r["id"], "Source": r["label"], "Kind": KIND_LABEL.get(r["kind"], r["kind"]),
                    "Market": market_label(r["region"]), "Channels": r["n_channels"], "Videos": r["n_videos"],
                    "Date": r["created_at"][:10],
                }
                for r in runs
            ]
        ),
        hide_index=True,
        width="stretch",
    )
run = similar.get_run(conn, run_id)
if not run:
    st.stop()
res = run["result"]
src = res["source"]

# ---------------------------------------------------------------------------
# Result: source, KPIs, how it was built
# ---------------------------------------------------------------------------

if src.get("video_id"):
    st.subheader(f"🎬 {src['video_title']}")
    st.caption(
        f"Channel **{src['channel']}** {src.get('handle') or ''} · {fmt_num(src['subs'])} subs · "
        f"video {fmt_num(src['video_views'])} views · [open video](https://www.youtube.com/watch?v={src['video_id']})"
    )
else:
    st.subheader(f"📺 {src['channel']}")
    st.caption(
        f"{src.get('handle') or ''} · {fmt_num(src['subs'])} subs · "
        f"[open channel](https://www.youtube.com/channel/{src['channel_id']})"
    )

stats = res["stats"]
n_err = len(stats.get("errors") or [])
theme.kpi_row([
    ("Channels found", len(res["channels"]), f"of {stats['candidates']['channels']} candidates"),
    ("Videos found", len(res["videos"]), f"of {stats['candidates']['videos']} candidates"),
    ("Units spent", stats["units"], "YouTube API quota"),
    ("Time", f"{stats['elapsed_s']:.0f} s", f"{n_err} failed calls" if n_err else "no errors", "inverse" if n_err else "off"),
])
if stats.get("errors"):
    st.warning(
        f"{n_err} YouTube calls (Up next / search) failed, so the result may be incomplete: "
        + "; ".join(stats["errors"][:3])
    )

with st.expander("How this list was built"):
    st.markdown(
        f"- Markets used for Up next: {', '.join(market_label(g) for g in res['locales'])}\n"
        f"- Seed videos: " + "; ".join(s["title"] for s in res["seeds"]) + "\n"
        f"- Search queries: " + "; ".join(f"`{q}`" for q in res["queries"]) + "\n"
        f"- Candidates collected: {stats['candidates']['channels']} channels, {stats['candidates']['videos']} videos; "
        f"after dropping off-topic ones: {len(res['channels'])} channels, {len(res['videos'])} videos\n"
        "- **Channel score** = 60% content match + 40% co-occurrence. **Video score** = 65% content match + "
        "35% number of appearances. Content match is relative to the closest candidate in this lookup (100% = closest).\n"
        "- Up next comes from an anonymous session, so it is noisy; the app drops candidates with content match below "
        f"{cfg.SIMILAR_MIN_CONTENT:.0%} and auto-generated music channels (“- Topic”)."
    )


def _sources(signals: dict) -> str:
    return " · ".join(f"{SIGNAL_LABEL.get(k, k)} ×{v:g}" for k, v in sorted(signals.items(), key=lambda kv: -kv[1]))


# ---------------------------------------------------------------------------
# Filters + tables
# ---------------------------------------------------------------------------

theme.section("Results", "Filter by channel size and video views, then open the Channels or Videos tab.")
f1, f2, f3 = st.columns(3)
min_subs = f1.number_input("Min subs", min_value=0, value=0, step=1000)
max_subs = f2.number_input("Max subs (0 = no limit)", min_value=0, value=0, step=10000)
min_views = f3.number_input("Min video views", min_value=0, value=0, step=1000)


def _subs_ok(subs) -> bool:
    if subs is None:
        return min_subs == 0
    return subs >= min_subs and (not max_subs or subs <= max_subs)


tab_c, tab_v = st.tabs([f"📺 Channels ({len(res['channels'])})", f"🎬 Videos ({len(res['videos'])})"])

with tab_c:
    rows = [
        {
            "Score": c["score"],
            "Channel": c["channel"],
            "Handle": c.get("handle") or "",
            "Subs": c["subs"],
            "Content match": round(100 * c["content"]),
            "Co-occurrence": round(100 * c["cooccur"]),
            "Sources": _sources(c["signals"]),
            "Recent videos": " | ".join(c.get("sample", [])),
            "Link": f"https://www.youtube.com/channel/{c['channel_id']}",
        }
        for c in res["channels"]
        if _subs_ok(c["subs"])
    ]
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Subs": st.column_config.NumberColumn(format="localized"),
                "Content match": st.column_config.NumberColumn(format="%d%%"),
                "Co-occurrence": st.column_config.NumberColumn(format="%d%%"),
                "Link": st.column_config.LinkColumn(display_text="Open channel"),
            },
        )
        st.download_button(
            "⬇️ Download channels CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"similar_channels_{run_id}.csv",
            mime="text/csv",
        )
    else:
        theme.empty_state("No channel matches the filters.")

with tab_v:
    fmts = st.multiselect(
        "Format", list(FMT_SHORT), default=list(FMT_SHORT), format_func=FMT_SHORT.get, key="similar_fmts"
    )
    rows = [
        {
            "Score": v["score"],
            "Title": v["title"],
            "Channel": v["channel"],
            "Views": v["views"],
            "Outlier": v.get("outlier"),
            "Level": v.get("level") or "",
            "Format": FMT_SHORT.get(v["fmt"], v["fmt"]),
            "Published": (v.get("published_at") or "")[:10],
            "Channel subs": v.get("subs"),
            "Sources": _sources(v["signals"]),
            "Link": f"https://www.youtube.com/watch?v={v['video_id']}",
        }
        for v in res["videos"]
        if v["fmt"] in fmts and (v["views"] or 0) >= min_views and _subs_ok(v.get("subs"))
    ]
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Views": st.column_config.NumberColumn(format="localized"),
                "Outlier": st.column_config.NumberColumn(
                    format="%.1f×", help="Views ÷ the channel's own median views (same format)"
                ),
                "Channel subs": st.column_config.NumberColumn(format="localized"),
                "Link": st.column_config.LinkColumn(display_text="Watch"),
            },
        )
        st.download_button(
            "⬇️ Download videos CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"similar_videos_{run_id}.csv",
            mime="text/csv",
        )
    else:
        theme.empty_state("No video matches the filters.")

st.divider()
if st.button("🗑️ Delete this lookup"):
    similar.delete_run(conn, run_id)
    st.session_state.pop("similar_run", None)
    st.rerun()
