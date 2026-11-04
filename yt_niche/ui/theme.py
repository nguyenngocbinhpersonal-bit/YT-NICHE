"""Shared look & feel for every Streamlit page: palette, CSS, page header, KPI tiles, pills, status icons.

See docs/UI_DESIGN_BRIEF.md. The Niche map page is the reference implementation.
"""

from __future__ import annotations

import streamlit as st

# Palette (data-viz method reference palette): blue ramp 100→700, diverging poles, ink and surfaces.
COLORS = {
    "blue": ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95"],
    "blue_pole": "#1c5cab",
    "red_pole": "#d03b3b",
    "mid": "#f0efec",
    "categorical": ["#2a78d6", "#eb6834", "#1baf7a"],  # first three slots validate for all-pairs charts
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "surface": "#fcfcfb",
    "surface2": "#f3f2ee",
    "line": "#e1e0d9",
    "none": "#e1e0d9",
    "group": "#f3f2ee",
}

# Charts use the same Google font as the app (.streamlit/config.toml loads it).
FONT = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
CHART_FONT_SIZE = 14

_CSS = """<style>
.yt-pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;font-weight:600;margin-right:6px;
         background:#f3f2ee;color:#52514e;border:1px solid #e1e0d9;line-height:20px}
.yt-pill.blue{background:#e4effc;color:#1c5cab;border-color:#b7d3f6}
.yt-pill.green{background:#e3f6e3;color:#0a5c0a;border-color:#b5e3b5}
.yt-pill.red{background:#fbe5e5;color:#9f2626;border-color:#f3b4b4}
.yt-pill.amber{background:#fff3d6;color:#7a5200;border-color:#f6dc9a}
.yt-sub{color:#52514e;font-size:17px;margin-top:-10px;margin-bottom:14px}
.yt-status{min-width:64px;text-align:center}
.yt-detail{color:#52514e;font-size:14px}
.yt-help{color:#898781;font-size:14px;margin-top:-6px;margin-bottom:8px}
.yt-legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#52514e;margin:2px 0 10px}
.yt-legend span.sw{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:5px;
                   border:1px solid rgba(11,11,11,.1)}
.yt-ramp{display:inline-block;width:110px;height:12px;border-radius:3px;vertical-align:-1px;margin:0 6px;
         border:1px solid rgba(11,11,11,.1)}
div[data-testid="stMetric"] label{color:#52514e}
/* Text values in KPI tiles (niche names) wrap instead of being cut off with an ellipsis. */
[data-testid="stMetricValue"] .stMarkdownEllipsis,[data-testid="stMetricValue"] .stMarkdownEllipsis p{white-space:normal !important;overflow:visible !important;text-overflow:clip !important;overflow-wrap:anywhere;line-height:1.15}
</style>"""


def inject_css() -> None:
    """Call once near the top of each page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def page_header(icon: str, title: str, subtitle: str | None = None) -> None:
    """`icon` is accepted for compatibility and ignored: the professional style uses no icons."""
    st.title(title)
    if subtitle:
        st.markdown(f"<div class='yt-sub'>{subtitle}</div>", unsafe_allow_html=True)


def kpi_row(items: list[tuple]) -> None:
    """items: (label, value, delta=None, delta_color="off"). 3–5 bordered stat tiles in one row."""
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        label, value = item[0], item[1]
        delta = item[2] if len(item) > 2 else None
        delta_color = item[3] if len(item) > 3 else "off"
        with col, st.container(border=True):
            st.metric(label, value, delta, delta_color=delta_color, delta_arrow="off")


def pill(text: str, kind: str = "") -> str:
    """Inline badge HTML. kind: '' | 'blue' | 'green' | 'red' | 'amber'. Render with st.markdown(..., unsafe_allow_html=True)."""
    return f"<span class='yt-pill {kind}'>{text}</span>"


def pills(*parts: str) -> None:
    st.markdown("".join(parts), unsafe_allow_html=True)


def section(title: str, help: str | None = None) -> None:
    st.markdown(f"### {title}")
    if help:
        st.markdown(f"<div class='yt-help'>{help}</div>", unsafe_allow_html=True)


def empty_state(message: str, hint: str | None = None) -> None:
    st.info(message + (f"\n\n{hint}" if hint else ""))


# Professional style: no icons anywhere. Status is carried by the word itself, inside a coloured badge where it matters.
# The *_ICON dicts are kept (empty values) so existing imports keep working; with_icon() returns the plain word.
LABEL_ICON = {"Green niche": "", "Promising niche": "", "Red niche": "", "Small sample": ""}
VERDICT_ICON = {"TRY": "", "TRY AFTER FIXING": "", "DROP": ""}
TIER_ICON = {"Strong": "", "Medium": "", "Weak": ""}
CONFIDENCE_ICON = {"high": "", "medium": "", "low": ""}
TREND_ICON = {"evergreen": "", "fad": "", "declining": "", "seasonal": ""}

# Tone per status word, for badges (pill kinds).
STATUS_TONE = {
    "Green niche": "green", "Promising niche": "amber", "Red niche": "red", "Small sample": "amber",
    "TRY": "green", "TRY AFTER FIXING": "amber", "DROP": "red",
    "Strong": "green", "Medium": "amber", "Weak": "red",
    "high": "green", "medium": "amber", "low": "red",
}


def status_pill(word: str | None, tone: str | None = None) -> str:
    """A status word in a coloured badge ('Done', 'Missing', 'TRY'…). The word carries the meaning, colour supports it."""
    if not word:
        return pill("—")
    return pill(word, tone if tone is not None else STATUS_TONE.get(word, ""))


def check_line(ok: bool | None, label: str, detail: str = "") -> str:
    """Checklist row without icons: a fixed-width status badge, the item in bold, then the detail."""
    word, tone = ("Done", "green") if ok else ("Open", "") if ok is None else ("To do", "amber")
    return f"<span class='yt-pill yt-status {tone}'>{word}</span> <b>{label}</b>" + (f" <span class='yt-detail'>{detail}</span>" if detail else "")


def label_icon(label: str | None) -> str:
    return LABEL_ICON.get(label or "", "")


def verdict_icon(verdict: str | None) -> str:
    return VERDICT_ICON.get(verdict or "", "")


def tier_icon(tier: str | None) -> str:
    return TIER_ICON.get(tier or "", "")


def with_icon(mapping: dict, value: str | None) -> str:
    """Kept for compatibility: returns the plain status word (the app uses no icons)."""
    return value or "—"
