# UI design brief — every page follows this

Goal: the app should read like one product, not ten scripts. The Niche map page (`yt_niche/ui/pages/niche_map_page.py`)
is the reference implementation; copy its patterns.

## Shared pieces (`yt_niche/ui/theme.py`) — use them, do not re-invent

| Helper | Use for |
|---|---|
| `inject_css()` | call once at the top of every page (after `get_conn()`); loads pills, legends, section styles |
| `page_header(icon, title, subtitle)` | the first thing on every page: `st.title` + one-line muted subtitle saying what the page is for (`icon` is ignored) |
| `kpi_row([(label, value, delta, delta_color), ...])` | 3–5 bordered stat tiles right under the header; values short, deltas as small context ("2 not ranked") |
| `pill(text, kind="")` | inline badge; kinds: `""` neutral, `"blue"` info, `"green"` good, `"red"` warning |
| `section(title, help=None)` | `###` heading with optional muted help line; use instead of bare `st.subheader` |
| `empty_state(message, hint=None)` | friendly `st.info` for "nothing here yet" + what to do next |
| `status_pill(word, tone=None)` | a status word in a coloured badge; tone comes from `STATUS_TONE` (Green niche → green, DROP → red…) |
| `check_line(ok, label, detail)` | checklist row: Done / To do / Open badge + bold item + muted detail |
| `COLORS` | the palette (blue ramp, red pole, ink, muted, surfaces); charts take colors from here |

## Style

Professional and quiet: neutral surfaces, one blue accent, typography and spacing do the work. No icons or emoji.
KPI deltas are context text without arrows (`delta_arrow="off"`).

## Typography

- Font: **Inter** from Google Fonts for text and headings, **JetBrains Mono** for code (`.streamlit/config.toml`).
  Both cover Vietnamese diacritics. Offline, the browser falls back to its default sans-serif.
- Base size **17px** (`baseFontSize`). Custom CSS sizes in `theme.py` are scaled to match; charts use `theme.FONT`
  and `theme.CHART_FONT_SIZE`.
- Nothing may be cut off with an ellipsis: give button columns explicit ratios wide enough for the label, and let
  text values in KPI tiles wrap (handled in `theme.inject_css`).

## Layout rules

1. **Header → KPIs → controls → content → details.** Controls (filters, forms) sit in one or two rows above the content
   they drive, never scattered. Use `st.columns` with explicit ratios so nothing truncates ("Ngách trên bả…" is a bug).
2. **Forms in a bordered container** (`st.container(border=True)`), primary action as `type="primary"`, one primary per form.
3. **Tables**: `st.dataframe(..., hide_index=True, width="stretch")`, 0–100 scores as `st.column_config.ProgressColumn`,
   money/views with `NumberColumn(format=...)`. Column names short, English, no abbreviations the user has to decode.
4. **No icons, no emoji — anywhere.** Not in navigation, titles, tabs, buttons, tables or charts. Status is a word,
   optionally in a coloured badge (`status_pill`): the word carries the meaning, the colour only supports it. Yes/No columns
   say "Yes". `tests/test_ui_smoke.py::test_no_icons_in_ui_source` enforces this.
5. **Details in tabs** when a record has several facets (keywords / channels / digs / log).
6. **Long explanations** go in `st.expander("How this is computed")`, not inline paragraphs.
7. **Charts**: Plotly, `config={"displayModeBar": False}`, transparent background, palette from `theme.COLORS`,
   one measure per channel (never size and color for the same variable), legend when ≥2 series, hover text full.
8. **Width**: everything `width="stretch"` (not the deprecated `use_container_width`).
9. **Copy**: short sentences, sentence case, no exclamation marks. Captions explain *what to do*, not what the code does.
10. **Quota and API-key warnings** live in the sidebar (common.quota_sidebar) — do not repeat them in page bodies.

## Page-by-page intent (what the user comes to do)

| Page | Purpose line (subtitle) |
|---|---|
| Dig | "Measure demand × supply × money with real numbers. One weak tier and the niche is weak." |
| Saved niches | "Every dig you ran. Re-dig for fresh numbers, compare old vs new." |
| Sub-niches & keywords | "Go one layer down: repeated phrases, search suggestions, keyword scores." |
| Niche map | "Niches Claude built from real data, sized by potential." |
| Similar videos & channels | "From one URL: who else serves this audience, and with what." |
| Compare | "Same topic across markets, side by side." |
| Scorecard | "Ten weighted criteria to lock in a main niche and a backup." |
| 10 test videos | "Plan ten videos, log the numbers, decide at video 5 and 10." |
| Stage progress | "Where you are in the Pick-a-niche stage, and what is due." |
| Keyword radar | "Google Trends categories scanned on a schedule; keywords that pass are worth a dig." |
| Settings | "API keys, markets, thresholds, AI and RPM table." |

Sidebar navigation titles (app.py): Dig · Saved niches · Sub-niches & keywords · Niche map · Similar videos & channels ·
Compare · Scorecard · 10 test videos · Stage progress · Keyword radar · Settings.
