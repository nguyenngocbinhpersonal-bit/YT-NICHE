"""Trang Bản đồ ngách: treemap nhiều chế độ nhóm (động cơ, mức tiềm năng, cây, ngôn ngữ, trend) + bảng chi tiết.

Trang chỉ đọc và vẽ. Việc dựng ngách, gán động cơ, gộp, xếp tiềm năng do Claude làm qua MCP (skill `yt-niche`).

Mã hoá (theo docs/NICHE_MAP_MCP_PLAN.md §7 và quy tắc dữ liệu-hình):
- diện tích = tiềm năng Claude đã xếp (ngách chưa xếp vẽ nhỏ, xám);
- màu = một thước đo KHÁC diện tích, chọn được: Cơ hội (nhu cầu − cạnh tranh, thang phân kỳ xanh ↔ đỏ, giữa xám),
  Nhu cầu (một màu xanh, nhạt → đậm), Độ mới số liệu (xanh, mới → đậm);
- viền đỏ + ⚠ = số liệu đã đổi so với lúc xếp (stale), cần Claude xếp lại.
Treemap là dạng "mọi ô kề nhau" nên không tô 10 màu cho 10 động cơ; động cơ hiện bằng nhóm / nhãn / huy hiệu.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.colors import sample_colorscale

from yt_niche.core.trends_classify import FAD, FALLING, SEASONAL, STABLE
from yt_niche.services import niche_map as nm
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn, market_label

conn = get_conn()
theme.inject_css()

MIN_SIZE = 3.0  # diện tích cho ngách chưa xếp tiềm năng
MODES = {"Motivation": "motivation", "Potential band": "potential", "Niche tree": "tree", "Language": "language", "Trend": "trend"}
COLOR_BY = {"Opportunity (demand − competition)": "opportunity", "Demand of main keywords": "demand", "Data freshness": "fresh"}
MOTIVATION_SHORT = {"learn": "Learn", "earn": "Earn", "fix": "Fix a problem", "improve": "Improve", "believe": "Believe & meaning",
                    "connect": "Connect", "escape": "Escape", "experience": "Experience", "updated": "Stay updated", "inspired": "Feel inspired"}
MOTIVATION_ICON = {"learn": "📘", "earn": "💰", "fix": "🔧", "improve": "📈", "believe": "🕯️", "connect": "🤝",
                   "escape": "🎮", "experience": "🧭", "updated": "📰", "inspired": "✨"}
TREND_LABEL = {STABLE: "Evergreen", SEASONAL: "Seasonal", FAD: "Fad", FALLING: "Declining"}
ROLE_LABEL = {"leader": "Leader", "rising": "Rising", "fallen": "Fallen"}
KIND_LABEL = {"main": "Main", "related": "Related", "longtail": "Long-tail", "trend": "Trend"}

# Bảng màu (references/palette.md của phương pháp dữ liệu-hình): xanh tuần tự 100→700, đỏ đối cực, xám giữa, trạng thái.
C = theme.COLORS
BLUE = C["blue"]
RED_POLE, BLUE_POLE, MID = C["red_pole"], C["blue_pole"], C["mid"]
NONE_FILL, GROUP_FILL, GROUP_LINE, TILE_LINE = C["none"], C["group"], "#c3c2b7", "#ffffff"
STALE_LINE = C["critical"]
INK, INK_2, MUTED = C["ink"], C["ink2"], C["muted"]
LANG_COLORS = C["categorical"]  # first three categorical slots validate for all-pairs charts



def _days_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def _potential_band(p: float | None) -> str:
    if p is None:
        return "Not ranked yet"
    return "Potential ≥ 70" if p >= 70 else "Potential 50–69" if p >= 50 else "Potential < 50"


def _seq(v: float | None, lo: float, hi: float) -> str:
    """Một màu xanh, nhạt → đậm theo v trong [lo, hi]; không có số → xám."""
    if v is None:
        return NONE_FILL
    t = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    return BLUE[round(t * (len(BLUE) - 1))]


def _div(v: float | None, span: float = 60.0) -> str:
    """Phân kỳ xanh ↔ đỏ, giữa xám: v > 0 (cầu hơn cung) xanh, v < 0 đỏ."""
    if v is None:
        return NONE_FILL
    t = max(-1.0, min(1.0, v / span))
    scale = [[0, RED_POLE], [0.5, MID], [1, BLUE_POLE]]
    return sample_colorscale(scale, [(t + 1) / 2])[0]


def _ink_on(fill: str) -> str:
    """Chữ trắng trên ô đậm, chữ đen trên ô nhạt (độ sáng tương đối)."""
    if not fill.startswith("#"):
        return INK
    r, g, b = (int(fill[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#ffffff" if lum < 0.5 else INK


@st.cache_data(ttl=10, show_spinner=False)
def _load(include_merged: bool) -> list[dict]:
    """Mỗi ngách: dòng list_tree + signals + stale + độ mới. Cache 10 giây để Claude ghi xong là thấy."""
    c = get_conn()
    basis = {r["id"]: json.loads(r["basis_json"] or "null") for r in c.execute("SELECT id, basis_json FROM niche")}
    out = []
    for row in nm.list_tree(c, include_merged=include_merged):
        sig = nm.signals(c, row["id"])
        opp = None
        if sig["main_demand"] is not None and sig["main_competition"] is not None:
            opp = round(sig["main_demand"] - sig["main_competition"], 1)
        out.append({
            **row, "signals": sig, "opportunity": opp,
            "stale": row["potential"] is not None and nm.is_stale(basis.get(row["id"]), sig),
            "days": _days_since(sig["latest_data_at"]),
        })
    return out


# ---------------------------------------------------------------------------
# Đầu trang: tiêu đề + thẻ số
# ---------------------------------------------------------------------------

theme.page_header("🗺️", "Niche map", "Niches Claude built from real data, sized by potential.")
rows_all = _load(include_merged=st.session_state.get("map_show_merged", False))
if not rows_all:
    st.info(
        "The map is empty. Open Claude Code in this folder and say: *\"use the yt-niche skill, build a niche from the keyword "
        "'psychology' for the VN market\"*. Claude fetches real numbers through MCP, builds niches, assigns motivations and ranks "
        "potential; this page redraws within 10 seconds."
    )
    st.stop()

live = [r for r in rows_all if r["status"] != "merged"]
ranked = [r for r in live if r["potential"] is not None]
top = max(ranked, key=lambda r: r["potential"]) if ranked else None
n_stale = sum(r["stale"] for r in live)
langs_live = sorted({r["language"] for r in live if r["language"]})
fresh = [r["days"] for r in live if r["days"] is not None]
theme.kpi_row([
    ("Niches", len(live), f"{len(live) - len(ranked)} not ranked" if len(live) > len(ranked) else "all ranked"),
    ("Top potential", f"{top['potential']:.0f}" if top else "—", top["name"] if top else None),
    ("Need re-rank", n_stale, "data changed" if n_stale else "map matches data", "inverse" if n_stale else "off"),
    ("Languages", len(langs_live), " · ".join(langs_live) or None),
    ("Freshest data", f"{min(fresh)} d ago" if fresh else "—", f"oldest {max(fresh)} d" if fresh else None),
])

# ---------------------------------------------------------------------------
# Bộ lọc (một hàng, trên biểu đồ)
# ---------------------------------------------------------------------------

r1a, r1b = st.columns([1.15, 1])
with r1a:
    mode = st.radio("Group by", list(MODES), horizontal=True, key="map_mode")
with r1b:
    color_by = COLOR_BY[st.radio("Color by", list(COLOR_BY), horizontal=True, key="map_color")]
languages = sorted({r["language"] for r in rows_all if r["language"]})
r2a, r2b, r2c, r2d = st.columns([1, 1.6, 1, 0.8])
with r2a:
    lang_pick = st.multiselect("Language", languages, default=languages, key="map_lang")
with r2b:
    motiv_pick = st.multiselect("Motivation", list(nm.MOTIVATIONS), default=list(nm.MOTIVATIONS),
                                format_func=lambda k: f"{MOTIVATION_ICON[k]} {MOTIVATION_SHORT[k]}", key="map_motiv")
with r2c:
    min_potential = st.slider("Min potential", 0, 100, 0, key="map_min_p")
with r2d:
    st.write("")
    st.checkbox("Show merged", key="map_show_merged")

rows = [
    r for r in rows_all
    if (not r["language"] or r["language"] in lang_pick)
    and (r["motivation"] is None or r["motivation"] in motiv_pick)
    and (r["potential"] is None or r["potential"] >= min_potential)
]
if not rows:
    st.warning("No niche matches the filters.")
    st.stop()

# ---------------------------------------------------------------------------
# Treemap
# ---------------------------------------------------------------------------

by_id = {r["id"]: r for r in rows}
mode_key = MODES[mode]
demands = [r["signals"]["main_demand"] for r in rows if r["signals"]["main_demand"] is not None]
d_lo, d_hi = (min(demands), max(demands)) if demands else (0, 100)
day_vals = [r["days"] for r in rows if r["days"] is not None]
day_hi = max(day_vals) if day_vals else 90


def _fill(r: dict) -> str:
    if r["status"] == "merged":
        return NONE_FILL
    if color_by == "opportunity":
        return _div(r["opportunity"])
    if color_by == "demand":
        return _seq(r["signals"]["main_demand"], d_lo, d_hi)
    return _seq(None if r["days"] is None else (day_hi - r["days"]), 0, max(day_hi, 1))


def _group_of(r: dict) -> str:
    if mode_key == "motivation":
        return f"{MOTIVATION_ICON.get(r['motivation'], '❔')} {MOTIVATION_SHORT.get(r['motivation'], 'No motivation yet')}"
    if mode_key == "potential":
        return _potential_band(r["potential"])
    if mode_key == "language":
        return {"vi": "🇻🇳 Vietnamese", "en": "🇺🇸 English", "ja": "🇯🇵 Japanese", "ko": "🇰🇷 Korean"}.get(r["language"], r["language"] or "Unknown language")
    if mode_key == "trend":
        return TREND_LABEL.get(r["signals"]["trend_class"], "No Trends data")
    return ""


def _parent_of(r: dict) -> str:
    keep_tree = mode_key in ("tree", "motivation", "language")
    if keep_tree and r["parent_id"] in by_id and _group_of(by_id[r["parent_id"]]) == _group_of(r):
        return f"n{r['parent_id']}"
    g = _group_of(r)
    return f"g:{g}" if g else ""


ids, labels, parents, values, colors, lines, widths, hover, custom, fonts = [], [], [], [], [], [], [], [], [], []
groups = {_group_of(r) for r in rows} - {""}
for g in sorted(groups):
    n_in = sum(1 for r in rows if _group_of(r) == g)
    ids.append(f"g:{g}"); labels.append(f"<b>{g}</b>  <span style='color:{MUTED}'>· {n_in}</span>"); parents.append("")
    values.append(0); colors.append(GROUP_FILL); lines.append(GROUP_LINE); widths.append(1)
    hover.append(f"<b>{g}</b><br>{n_in} niches"); custom.append(0); fonts.append(INK_2)
parent_ids = {_parent_of(r) for r in rows}
for r in rows:
    sig, p = r["signals"], r["potential"]
    fill = _fill(r)
    ids.append(f"n{r['id']}")
    flag = " ⚠" if r["stale"] else ""
    tag = " · merged" if r["status"] == "merged" else ""
    meta = f"{MOTIVATION_ICON.get(r['motivation'], '')} {r['language'] or ''}".strip()
    opp = f"{r['opportunity']:+.0f}" if r["opportunity"] is not None else "—"
    shown = '%.0f' % p if p is not None else '—'
    if f"n{r['id']}" in parent_ids:  # ngách mẹ có con: nhãn một dòng nằm ở dải tiêu đề phía trên các con
        labels.append(f"<b>{r['name']}</b>{flag}{tag} · {shown} · {meta} · opp {opp}")
    else:
        labels.append(
            f"<b>{r['name']}</b>{flag}{tag}<br>"
            f"<span style='font-size:20px'>{shown}</span>"
            f"<span style='font-size:11px'>  {meta} · opp {opp}</span>"
        )
    parents.append(_parent_of(r))
    values.append(p if p is not None else MIN_SIZE)
    colors.append(fill)
    lines.append(STALE_LINE if r["stale"] else TILE_LINE)
    widths.append(3 if r["stale"] else 2)
    fonts.append(_ink_on(fill))
    hover.append(
        f"<b>{r['path']}</b><br>"
        f"{MOTIVATION_ICON.get(r['motivation'], '')} {MOTIVATION_SHORT.get(r['motivation'], 'no motivation yet')}"
        f"{' + ' + MOTIVATION_SHORT[r['motivation_secondary']] if r['motivation_secondary'] else ''} · language {r['language'] or '?'}<br>"
        f"<b>Potential {'%.0f' % p if p is not None else 'not ranked'}</b>{' — data changed, needs re-rank' if r['stale'] else ''}<br>"
        f"Demand {sig['main_demand'] if sig['main_demand'] is not None else '—'} · Competition "
        f"{sig['main_competition'] if sig['main_competition'] is not None else '—'} · Opportunity {opp}<br>"
        f"Digs {sig['n_runs']} (best {('%.0f' % sig['best_run_score']) if sig['best_run_score'] is not None else '—'}) · "
        f"small booming channels {sig['small_boom_channels']} · trend {TREND_LABEL.get(sig['trend_class'], '—')}<br>"
        f"Keywords {sum(sig['n_keywords'].values())} · data {str(r['days']) + ' d ago' if r['days'] is not None else 'none yet'}"
    )
    custom.append(r["id"])

fig = go.Figure(
    go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values, customdata=custom,
        branchvalues="remainder", hovertext=hover, hoverinfo="text",
        marker=dict(colors=colors, line=dict(color=lines, width=widths), cornerradius=6, pad=dict(t=28, l=4, r=4, b=4)),
        textfont=dict(color=fonts, size=13), insidetextfont=dict(color=fonts),
        textposition="middle center", tiling=dict(packing="squarify", pad=3),
        pathbar=dict(visible=True, thickness=24, textfont=dict(size=12, color=INK_2), edgeshape=">"),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor="#e1e0d9", font=dict(color=INK, size=12)),
    )
)
fig.update_layout(
    margin=dict(t=4, l=0, r=0, b=0), height=600, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    uniformtext=dict(minsize=11, mode="hide"), font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"),
)
event = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="map_chart",
                        config={"displayModeBar": False})

# Chú giải nằm dưới biểu đồ: màu đang dùng + viền stale + ô xám
if color_by == "opportunity":
    ramp = f"linear-gradient(90deg,{RED_POLE},{MID},{BLUE_POLE})"
    legend = (f"<span><b>Color</b> = demand − competition: more competition <span class='yt-ramp' style='background:{ramp}'></span> more demand</span>")
elif color_by == "demand":
    ramp = f"linear-gradient(90deg,{BLUE[0]},{BLUE[-1]})"
    legend = f"<span><b>Color</b> = demand of main keywords: low <span class='yt-ramp' style='background:{ramp}'></span> high</span>"
else:
    ramp = f"linear-gradient(90deg,{BLUE[0]},{BLUE[-1]})"
    legend = f"<span><b>Color</b> = data freshness: old <span class='yt-ramp' style='background:{ramp}'></span> fresh</span>"
st.markdown(
    f"<div class='yt-legend'><span><b>Area</b> = potential ranked by Claude</span>{legend}"
    f"<span><span class='sw' style='background:{NONE_FILL}'></span>no data / merged</span>"
    f"<span><span class='sw' style='background:#fff;border:2px solid {STALE_LINE}'></span>⚠ data changed, needs re-rank</span>"
    f"<span style='color:{MUTED}'>Click a tile for details · click a group name to zoom</span></div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Chọn ngách: từ ô vừa bấm, hoặc hộp chọn
# ---------------------------------------------------------------------------

picked_id = None
try:
    pts = event.selection.points if event and event.selection else []
    for pt in pts:
        cd = pt.get("customdata")
        cd = cd[0] if isinstance(cd, list) else cd
        if cd:
            picked_id = int(cd)
            break
except Exception:  # cấu trúc selection khác giữa các bản Streamlit: rơi về hộp chọn
    picked_id = None

options = [r["id"] for r in rows]
if picked_id in options:
    st.session_state["map_pick"] = picked_id
if st.session_state.get("map_pick") not in options:
    st.session_state["map_pick"] = options[0]

# ---------------------------------------------------------------------------
# Chi tiết
# ---------------------------------------------------------------------------

with st.container(border=True):
    niche_id = st.selectbox("Niche", options, format_func=lambda i: by_id[i]["path"], key="map_pick")
    d = nm.get(conn, niche_id)
    sig = d["signals"]
    pills = [f"<span class='yt-pill blue'>{d['language'] or '?'}</span>"]
    if d["motivation"]:
        pills.append(f"<span class='yt-pill'>{MOTIVATION_ICON[d['motivation']]} {MOTIVATION_SHORT[d['motivation']]}</span>")
    if d["motivation_secondary"]:
        pills.append(f"<span class='yt-pill'>+ {MOTIVATION_SHORT[d['motivation_secondary']]}</span>")
    pills.append(f"<span class='yt-pill'>{d['status']}</span>")
    if d["stale"]:
        pills.append("<span class='yt-pill red'>⚠ needs re-rank</span>")
    st.markdown(f"### {d['name']}")
    st.markdown("".join(pills), unsafe_allow_html=True)
    if d["summary"]:
        st.write(d["summary"])

    m1, m2, m3, m4, m5 = st.columns(5)
    opp = None if sig["main_demand"] is None or sig["main_competition"] is None else sig["main_demand"] - sig["main_competition"]
    m1.metric("Potential", f"{d['potential']:.0f}" if d["potential"] is not None else "not ranked")
    m2.metric("Demand", sig["main_demand"] if sig["main_demand"] is not None else "—", "main keywords", delta_color="off")
    m3.metric("Competition", sig["main_competition"] if sig["main_competition"] is not None else "—",
              f"opportunity {opp:+.0f}" if opp is not None else None, delta_color="normal" if opp is not None and opp >= 0 else "inverse")
    m4.metric("Best dig score", f"{sig['best_run_score']:.0f}" if sig["best_run_score"] is not None else "—",
              f"{sig['n_runs']} digs", delta_color="off")
    m5.metric("Small booming channels", sig["small_boom_channels"], TREND_LABEL.get(sig["trend_class"], "no Trends data"), delta_color="off")
    if d["potential_rationale"]:
        st.caption(f"🤖 Ranking rationale ({(d['ranked_at'] or '')[:10]}): {d['potential_rationale']}")

    if d["status"] == "merged":
        target = by_id[d["merged_into"]]["name"] if d["merged_into"] in by_id else f"#{d['merged_into']}"
        st.warning(f"This niche was merged into **{target}**. Its keywords, digs and channels moved there.")
        c1, c2 = st.columns([3, 1])
        why = c1.text_input("Reason to unmerge", key=f"unmerge_why_{niche_id}", placeholder="merged by mistake: different audience…")
        if c2.button("Unmerge", key=f"unmerge_{niche_id}", type="primary", width="stretch"):
            try:
                nm.unmerge(conn, niche_id, why, actor="user")
                _load.clear()
                st.success("Unmerged.")
                st.rerun()
            except nm.NicheError as e:
                st.error(str(e))

    tab_kw, tab_ch, tab_run, tab_ev = st.tabs(["🔑 Keywords", "📺 Competitors", "⛏️ Digs", "📝 Decision log"])
    with tab_kw:
        cols = st.columns(2) + st.columns(2)  # 2 hàng × 2 cột để bảng đủ rộng
        for col, kind in zip(cols, nm.KINDS):
            with col:
                kws = d["keywords"][kind]
                st.markdown(f"**{KIND_LABEL[kind]}** <span class='yt-pill'>{len(kws)}</span>", unsafe_allow_html=True)
                if kws:
                    st.dataframe(
                        pd.DataFrame([{
                            "Keyword": k["keyword"], "Market": k["region"], "Demand": k["demand"], "Competition": k["competition"],
                            "Score": k["score"], "Scored": (k["scored_at"] or "")[:10] or "not yet",
                        } for k in kws]),
                        hide_index=True, width="stretch",
                        column_config={
                            "Demand": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                            "Competition": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                        },
                    )
                else:
                    st.caption("—")
    with tab_ch:
        if d["channels"]:
            st.dataframe(
                pd.DataFrame([{
                    "Role": ROLE_LABEL.get(c["role"], c["role"]), "Channel": c["title"] or c["channel_id"], "Subs": c["subs"],
                    "Median long": c["median_long"], "Median Shorts": c["median_short"], "Note": c["note"],
                    "Updated": (c["updated_at"] or "")[:10],
                } for c in d["channels"]]),
                hide_index=True, width="stretch",
            )
        else:
            st.caption("No channels yet. Claude attaches them with `niche_set_channels` after a dig or a similar-channel lookup.")
    with tab_run:
        if d["runs"]:
            st.dataframe(
                pd.DataFrame([{
                    "#": r["id"], "Topic": r["topic"], "Market": market_label(r["region"]), "Date": r["created_at"][:10],
                    "Score": r["score"], "Label": r["label"], "Confidence": r["confidence"], "Verdict": r["verdict"],
                } for r in d["runs"]]),
                hide_index=True, width="stretch",
                column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")},
            )
        else:
            st.caption("No dig attached to this niche yet.")
    with tab_ev:
        for e in d["events"]:
            who = "🤖 Claude" if e["actor"] == "claude" else "👤 You" if e["actor"] == "user" else "⚙️ App"
            st.markdown(
                f"<span class='yt-pill'>{e['kind']}</span> {who} · <span style='color:{MUTED}'>{e['created_at'][:16].replace('T', ' ')}</span>"
                f"<br>{e['rationale'] or '<i>no rationale recorded</i>'}",
                unsafe_allow_html=True,
            )

# ---------------------------------------------------------------------------
# Bubble: cầu cao, cung yếu
# ---------------------------------------------------------------------------

with st.expander("Bubble view: demand × (100 − competition), size = potential, color = language"):
    pts = [r for r in rows if r["signals"]["main_demand"] is not None and r["signals"]["main_competition"] is not None]
    if not pts:
        st.caption("Needs at least one niche with scored main keywords.")
    else:
        fig2 = go.Figure()
        langs = sorted({r["language"] for r in pts})
        for i, lang in enumerate(langs[:3]):
            part = [r for r in pts if r["language"] == lang]
            fig2.add_trace(go.Scatter(
                x=[r["signals"]["main_demand"] for r in part], y=[100 - r["signals"]["main_competition"] for r in part],
                mode="markers+text", text=[r["name"] for r in part], textposition="top center", name=lang,
                textfont=dict(color=INK_2, size=12),
                marker=dict(size=[(r["potential"] or MIN_SIZE) * 0.6 + 10 for r in part], sizemode="diameter",
                            color=LANG_COLORS[i], opacity=0.8, line=dict(color="#ffffff", width=2)),
                hovertext=[f"{r['name']}: potential {r['potential'] if r['potential'] is not None else '—'}" for r in part], hoverinfo="text",
            ))
        fig2.add_shape(type="rect", x0=50, x1=100, y0=50, y1=100, fillcolor="#e4effc", opacity=0.35, line_width=0, layer="below")
        fig2.add_annotation(x=98, y=98, text="high demand · weak supply", showarrow=False, font=dict(color=INK_2, size=11), xanchor="right")
        fig2.update_layout(
            xaxis=dict(title="Demand of main keywords", range=[0, 100], gridcolor="#e1e0d9", zeroline=False),
            yaxis=dict(title="100 − competition", range=[0, 100], gridcolor="#e1e0d9", zeroline=False),
            height=460, margin=dict(t=10, r=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(title="Language", orientation="h", y=-0.18), font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"),
        )
        st.plotly_chart(fig2, width="stretch", key="map_bubble", config={"displayModeBar": False})
