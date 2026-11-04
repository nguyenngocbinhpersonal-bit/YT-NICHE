# YT Niche

A local YouTube niche-research tool. It measures a niche with real numbers (YouTube Data API, Google Trends),
scores it, and keeps everything in one SQLite file. Claude Code can drive the app through an MCP server to build and
maintain a **niche map**: niches grouped by audience motivation, sized by potential, with every decision logged.

## What the app does

| Page | Purpose |
|---|---|
| Dig | Measure demand × supply × money for a topic in one market. Niche score, four bars, three tiers, small booming channels, Trends shape. |
| Saved niches | Every dig you ran. Re-dig for fresh numbers and compare old vs new. |
| Sub-niches & keywords | Go one layer down: repeated phrases in winning titles, search suggestions, keyword scores from real search results. |
| Niche map | Niches Claude built from real data, in a treemap sized by potential; grouped by motivation, potential band, tree, language or trend. |
| Similar videos & channels | From one video or channel URL: who else serves this audience. |
| Compare | Same topic across markets, side by side. |
| Scorecard | Ten weighted criteria to lock in a main niche and a backup. |
| 10 test videos | Plan ten videos, log the numbers, decide at video 5 and 10. |
| Stage progress | Where you are in the Pick-a-niche stage and what is due. |
| Keyword radar | Google Trends categories scanned on a schedule; keywords that pass are worth a dig. |
| Settings | API keys, markets, thresholds, AI, RPM table. |

## Setup

```bash
pip install -e ".[dev,mcp]"
```

Put your YouTube Data API key in `.env` as `YOUTUBE_API_KEY=...` (or enter it on the Settings page). Then:

```bash
streamlit run yt_niche/ui/app.py
```

## Claude Code and the niche map

Open Claude Code in this folder. `.mcp.json` starts the MCP server (`python -m yt_niche.mcp`) and the skill in
`.claude/skills/yt-niche/` tells Claude how to work:

- the app does the mechanical work (fetch, score, store, draw);
- Claude does the judgement (keywords, naming, taxonomy, motivation, merging, ranking) and records a rationale for
  every change;
- one niche = one language; data from another language is refused at the database level.

Try: *"use the yt-niche skill, build a niche from the keyword 'psychology' for the VN market"*. The Niche map page
redraws within ten seconds.

Design notes: `docs/NICHE_MAP_MCP_PLAN.md` (architecture), `docs/UI_DESIGN_BRIEF.md` (UI rules),
`docs/GLOSSARY_EN.md` (terms and stored constants).

## Tests

```bash
python -m pytest -q
```

Streamlit pages are smoke-tested with `streamlit.testing` against a temporary database, so no API calls are made.
