---
name: yt-niche
description: Build and update a YouTube niche map with the yt-niche MCP server. Use when the user gives a keyword, a channel, a video URL, or describes a context and wants to build niches, classify niches by audience motivation, merge niches, rank potential, or update competitors. Also use when asked "which niche has the most potential", "update the map", "what motivation does this niche belong to".
---

# YouTube niche map through MCP

You judge; the app measures. You never invent numbers: every number (demand, competition, dig score, trend) comes from
a tool of the `yt-niche` MCP server. You name, build the tree, assign motivations, merge, and rank potential **after**
reading the real numbers from the DB. Every decision carries a 1–2 sentence `rationale` in English.

## One niche = one language

Every niche has a fixed `language` (vi, en, ja…), chosen at creation. All of a niche's data must be in that language;
the app enforces it in the DB, but you must choose correctly from the start so no quota is spent on data that gets thrown away:

- **Pick the language in step 1**, before fetching any data. Entry is a keyword → the keyword's language. Entry is a
  channel / URL → the language of the source video titles. Entry is a context → ask the user if unclear.
- Every call to `keywords_suggest`, `keywords_score`, `niche_dig`, `similar_from_url` uses **a region of that language**
  (`niche_get` returns `regions_allowed`; en = US, GB, CA, AU, PH; vi = VN; ja = JP…). Never call a region of another
  language "to see more".
- Reading `similar_from_url` results: a channel whose `country` is outside `regions_allowed`, or whose titles / tags are in
  another language, is **dropped**: not a competitor, not a keyword source. `competitors_refresh` flags
  `language_mismatch` on channels that declare a country / language different from the niche; such channels do not go into
  `niche_set_channels` unless you checked and the content is in the right language (say so in the rationale).
- The same topic in another language is **another niche**, created separately ("Tâm lý học" in vi and "Psychology facts"
  in en are two niches). Never merge them, never nest one under the other. To compare two languages, compare two niches
  on `map_compare`.
- Translated keywords: use the app's `translate_keywords` prompt (or translate yourself), then fetch real numbers in the
  target market; never attach a Vietnamese keyword to an English niche even when the meaning is the same.

## Budget first, data second

1. Call `quota_status` at the start of the session. `spendable` is the number of YouTube units this session may spend.
2. Costs: `keywords_suggest` 0 units; `keywords_score` ≈102 units/keyword; `niche_dig` ≈210–350; `similar_from_url` ≈10–30;
   `competitors_refresh` ≈2/channel. Tools refuse by themselves when they would eat into the share reserved for the UI;
   use `force=true` only when the user explicitly agrees.
3. Cheap first: search suggestions (0 units) for related/longtail, `radar_keywords` for trend, `keywords_list` and
   `runs_list` to reuse existing numbers. A niche dug within the last 90 days makes `niche_dig` return that dig.

## Workflow to build / update a niche

```
1. Take the entry: keyword | channel | video URL | context description. Fix the language and region used throughout.
2. Extract seeds, in the entry's language:
   - keyword        → itself + 2–4 variants you infer
   - channel / URL  → similar_from_url → read seed_titles, queries, the source channel's keywords → extract 3–6 repeated phrases
   - context        → you propose 5–10 seeds, clearly marked as hypotheses
3. keywords_suggest for each seed (0 units). Collect candidates.
4. Classify candidates by intent:
   main     = 2–5 short keywords that represent the niche; will be scored with real numbers
   related  = same topic, different angle
   longtail = ≥ 4 words, a specific question / situation
   trend    = present in radar_keywords or tied to an event / new product
5. keywords_score for at most 10 main keywords (only main keywords need real numbers). Anything low_data drops to related.
6. niche_find_by_keywords with the main keywords → does a niche already hold them?
   - yes, same language, ≥ 60% main+related overlap, same primary motivation → attach to that niche (or niche_merge if there are 2)
   - yes, little overlap                                                       → create a child or sibling niche
   - no                                                                        → niche_create
7. niche_dig the 1–2 most important main keywords (if no run exists yet) → niche_attach_run.
8. niche_attach_keywords (correct kind; numbers are copied automatically).
9. niche_set_motivation using the table below. One primary, at most one secondary.
10. Channels: from the run (small_boom_channels, top_outliers) and similar_from_url → niche_set_channels with a role.
11. End of session: map_compare → niche_rank for the niches you touched and the stale ones.
12. Report to the user: quota spent, which niches were created / merged / re-ranked, which niches still lack numbers.
```

## Ranking potential (niche_rank)

Rank only after reading `map_compare`. The 0–100 scale is a **relative comparison between the niches on the map**, not an
absolute score. Weigh in this order: main keyword demand and median views → competition (lower is better) and the number
of small booming channels → trend (evergreen > seasonal > fad > declining) → dig score and three-tier verdict → money
(income_per_video). A niche without numbers (`n_main_scored = 0` and `n_runs = 0`) is not ranked; say it lacks data.
A niche with `stale = true` must be re-ranked. The `rationale` quotes the numbers you relied on, e.g. "demand 68,
competition 22, 4 small booming channels, evergreen trend; above Money psychology (demand 51, competition 40)".

## The ten audience motivations

| Code | What the audience is asking | Signals in the main keywords |
|---|---|---|
| learn | what X is, how it works | what is, explained, history, how it works, why |
| earn | how to make money from X | make money, invest, career, income, sell |
| fix | X is broken, how to fix it | how to fix, error, not working, troubleshoot |
| improve | do X better | tips, optimise, advanced, habits, practice |
| believe | what this means to me | spirituality, philosophy, ethics, purpose in life |
| connect | how people like me live | vlog, a day in the life, reaction, community |
| escape | let me forget for a while | pure entertainment, gaming, comedy, ASMR, music |
| experience | what X feels like | review, trying, travel, unboxing, taste test |
| updated | what's new about X | news, update, latest, this week, leak |
| inspired | give me motivation | story, journey, from zero to, motivation |

Look at the **intent of the main keywords**, not the topic. Two motivations tied → the one with higher demand becomes primary.

## Merge criteria (niche_merge)

Merge when all three hold: same language, ≥ 60% overlap of main+related keywords, same primary motivation. A merge is
soft and can be undone with `niche_unmerge`, but the rationale must still state where the two niches overlap. Never merge
a parent into its child.

## Refresh mode ("update the map", "refresh")

1. `map_list` → for every active niche: `competitors_refresh` (cheap), read which channels rose / fell → `niche_set_channels`.
2. Main keywords with `scored_at` older than 30 days → `keywords_score` again (respecting quota), then `niche_refresh_numbers`.
3. `map_compare` → re-rank every niche with `stale = true`. Leave unchanged niches alone.
4. Report: which niches moved up / down and why.

## Things not to do

- Never mix languages: no keyword / dig / channel of another language on a niche; never merge niches of different languages.
- Never use `kind=main` for a keyword without numbers. Never rank a niche without numbers.
- Never re-dig a niche with a run younger than 90 days unless the user asks for `fresh`.
- Never delete anything; `niche_drop` and `niche_merge` both keep the data.
- Never call `force=true` without asking the user.
