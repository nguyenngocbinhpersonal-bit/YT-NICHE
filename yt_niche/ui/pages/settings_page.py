"""Settings page: API keys, AI, Google Trends backup (DataForSEO), RPM table, markets, scoring thresholds."""

import asyncio

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche import settings
from yt_niche.db import runs as run_repo
from yt_niche.services import ai
from yt_niche.services import market_catalog as mc
from yt_niche.services.runs import reanalyze
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.youtube import YouTubeClient, YouTubeError
from yt_niche.ui import theme
from yt_niche.ui.common import get_conn

conn = get_conn()
theme.inject_css()
theme.page_header("⚙️", "Settings", "API keys, markets, thresholds, AI and RPM table.")

tab_key, tab_ai, tab_gt, tab_rpm, tab_mk, tab_thr = st.tabs(
    ["🔑 YouTube API key", "🤖 AI", "📈 Google Trends", "💵 RPM table", "🌐 Markets", "🎚️ Thresholds"]
)

# ---------------------------------------------------------------------------
with tab_key:
    with st.container(border=True):
        theme.section("YouTube Data API key", "Every dig, keyword score and similar-channel lookup spends quota from this key.")
        key = cfg.api_key()
        if key:
            theme.pills(theme.pill(f"🟢 Key in use: {key[:4]}…{key[-4:]}", "green"))
        else:
            theme.pills(theme.pill("🔴 No key yet", "red"))
        with st.expander("Where to get a key"):
            st.markdown("Google Cloud Console → enable *YouTube Data API v3* → Credentials → Create API key. The key is stored in the `.env` file.")
        new_key = st.text_input("New key", type="password")
        c1, c2, _ = st.columns([1, 1.4, 3])
        if c1.button("Save key", type="primary", disabled=not new_key.strip(), width="stretch"):
            cfg.save_api_key(new_key)
            st.success("Saved to .env.")
            st.rerun()
        if c2.button("Test key (1 unit)", disabled=not key, width="stretch"):

            async def _check():
                async with YouTubeClient(conn) as yt:
                    return await yt.videos(["jNQXAC9IVRw"], ttl=0)  # the first video ever uploaded to YouTube

            try:
                asyncio.run(_check())
                st.success("The key works.")
            except YouTubeError as e:
                st.error(str(e))

# ---------------------------------------------------------------------------
with tab_ai:
    ai_cfg = ai.get_config(conn)
    with st.container(border=True):
        theme.section("AI", "Optional. Classifies radar keywords and runs the prompts inside the app. The app works without it.")
        with st.expander("What is sent to the AI"):
            st.markdown(
                "Any OpenAI-compatible API (Command Code by default). Only public data goes out: keywords, titles, numbers and "
                "the prompt text. Never your email or YouTube API key. If AI is off or fails, the app runs as before."
            )
        if ai.api_key():
            theme.pills(theme.pill(f"🟢 Key in use: {ai.masked_key()}", "green"))
        else:
            theme.pills(theme.pill("🔴 No AI key yet", "red"))
        new_ai_key = st.text_input("New key (COMMANDCODE_API_KEY, stored in .env)", type="password", key="ai_new_key")
        k1, k2, _ = st.columns([1, 2, 2])
        if k1.button("Save AI key", disabled=not new_ai_key.strip(), width="stretch"):
            ai.save_api_key(new_ai_key)
            st.rerun()
        base_url = st.text_input("API base URL", ai_cfg["base_url"], help=f"Command Code default: {cfg.AI_BASE_URL}")
        if k2.button("Test connection and load model list", disabled=not ai.api_key(), width="stretch"):
            try:
                models = ai.list_models(base_url)
                ai.save_config(conn, {"base_url": base_url.strip(), "models": models})
                st.success(f"Connected: {len(models)} models.")
                for tier in ai.TIERS:
                    st.session_state.pop(f"ai_model_{tier}", None)
                st.rerun()
            except ai.AIError as e:
                st.error(str(e))

        # Key present but no model list yet: load it once so the user can pick from a list.
        if ai.api_key() and not ai_cfg["models"] and not st.session_state.get("ai_models_tried"):
            st.session_state["ai_models_tried"] = True
            try:
                ai_cfg["models"] = ai.list_models(ai_cfg["base_url"])
                ai.save_config(conn, {"models": ai_cfg["models"]})
            except ai.AIError as e:
                st.warning(f"Could not load the model list: {e}")
        models = ai_cfg["models"]
        picked_models, picked_effort = {}, {}
        st.markdown("**Models**")
        st.caption("The fast model should be cheap (the radar calls it often); the deep model runs when you press \"Run with AI\" on an analysis prompt.")
        for tier in ai.TIERS:
            m_col, e_col = st.columns([3, 1])
            current = ai_cfg[f"model_{tier}"]
            opts = list(dict.fromkeys(([current] if current else []) + models))
            picked_models[tier] = m_col.selectbox(
                f"Model: {ai.TIERS[tier]}", opts, index=opts.index(current) if current else None,
                placeholder="Pick a model (or type a name)" if opts else "Type a model name or press \"Test connection\"",
                accept_new_options=True, key=f"ai_model_{tier}",
            )
            effort = ai_cfg[f"effort_{tier}"]
            picked_effort[tier] = e_col.selectbox(
                "Effort", list(ai.EFFORTS), index=list(ai.EFFORTS).index(effort) if effort in ai.EFFORTS else 0,
                format_func=ai.EFFORTS.get, key=f"ai_effort_{tier}",
                help="Reasoning effort for models that support it: higher is more thorough but slower and costs more tokens. "
                     "Ignored by models that do not support it.",
            )
        st.markdown("**Features**")
        feats = {f: st.checkbox(label, ai_cfg["features"].get(f, True), key=f"ai_feat_{f}") for f, label in ai.FEATURES.items()}
        limit = st.number_input("Max AI requests per day", 1, 100000, int(ai_cfg["max_requests_per_day"]),
                                help="When the limit is reached the AI pauses until tomorrow; the app keeps working.")
        on = st.toggle("AI on", ai_cfg["enabled"])
        u = ai.usage_today(conn)
        st.caption(f"Today: {u['requests']}/{ai_cfg['max_requests_per_day']} requests, {u['prompt_tokens']:,} tokens in, {u['completion_tokens']:,} tokens out.")
        s1, s2, _ = st.columns([1, 1.4, 3])
        if s1.button("Save AI settings", type="primary", width="stretch"):
            if on and not (ai.api_key() and picked_models["fast"] and picked_models["deep"]):
                st.error("Add a key and pick both models before turning AI on.")
            else:
                ai.save_config(conn, {"enabled": on, "base_url": base_url.strip(), "model_fast": (picked_models["fast"] or "").strip(),
                                      "model_deep": (picked_models["deep"] or "").strip(), "features": feats,
                                      "effort_fast": picked_effort["fast"], "effort_deep": picked_effort["deep"],
                                      "max_requests_per_day": int(limit)})
                st.success("Saved.")
                st.rerun()
        if ai.enabled(conn) and s2.button("Ask a test question (1 request)", width="stretch"):
            try:
                st.info(ai.chat(conn, "fast", [{"role": "user", "content": "Answer with exactly one word: what is the capital of France?"}], max_tokens=20))
            except ai.AIError as e:
                st.error(str(e))

# ---------------------------------------------------------------------------
with tab_gt:
    d_cfg = dfs.get_config(conn)
    with st.container(border=True):
        theme.section("DataForSEO backup for Google Trends", "Paid fallback used only while Google blocks the free Trends endpoint (429).")
        with st.expander("How the backup works and what it costs"):
            st.markdown(
                "Google Trends is free through Google, but Google often blocks the machine (error 429) for tens of minutes. "
                "The app can then fetch the same data from DataForSEO (pay per task, about $0.009 each; one task = one series "
                "of up to 5 keywords). Only keywords, country code, Web/YouTube source and category id are sent."
            )
        if all(dfs.credentials()):
            theme.pills(theme.pill(f"🟢 DataForSEO login in use: {dfs.masked_login()}", "green"))
        else:
            theme.pills(theme.pill("🔴 No DataForSEO login yet", "red"))
            st.caption("Create an account at dataforseo.com → Dashboard → API Access (API login and password).")
        l_col, p_col = st.columns(2)
        new_login = l_col.text_input("API login", key="dfs_login")
        new_pass = p_col.text_input("API password", type="password", key="dfs_pass")
        b1, b2, _ = st.columns([1, 1.4, 3])
        if b1.button("Save login", disabled=not (new_login.strip() and new_pass.strip()), width="stretch"):
            dfs.save_credentials(new_login, new_pass)
            st.rerun()
        if b2.button("Test connection (free)", disabled=not all(dfs.credentials()), width="stretch"):
            try:
                st.success(f"Connected. Balance: ${dfs.account_balance():,.2f}")
            except dfs.BackupError as e:
                st.error(str(e))
        st.markdown("**Use the backup for**")
        d_feats = {f: st.checkbox(label, True if f == "dig" else d_cfg["radar"], disabled=f == "dig", key=f"dfs_feat_{f}")
                   for f, label in dfs.FEATURES.items()}
        d_limit = st.number_input(
            "Max DataForSEO tasks per day", 1, 10000, int(d_cfg["max_tasks_per_day"]),
            help="When the limit is reached the app waits for Google as before. 40 tasks ≈ $0.36 per day.",
        )
        d_on = st.toggle("Use DataForSEO when Google blocks", d_cfg["enabled"], key="dfs_on")
        du = dfs.usage_today(conn)
        st.caption(f"Today: {du['tasks']}/{d_cfg['max_tasks_per_day']} tasks, cost ${du['cost']:.3f}.")
        if st.button("Save Google Trends settings", type="primary"):
            if d_on and not all(dfs.credentials()):
                st.error("Save the DataForSEO login and password before turning this on.")
            else:
                dfs.save_config(conn, {"enabled": d_on, "radar": d_feats["radar"], "max_tasks_per_day": int(d_limit)})
                st.success("Saved.")
                st.rerun()

# ---------------------------------------------------------------------------
with tab_rpm:
    with st.container(border=True):
        theme.section("RPM table", "Estimated RPM (USD per 1,000 views) per market × topic category. Edit the numbers, add rows for new categories.")
        with st.expander("Where the defaults come from"):
            st.markdown(
                "The defaults are rough estimates meant for ranking niches against each other, not for forecasting income. "
                "Replace them with your own real numbers whenever you have them. After saving, press \"Recompute all digs\" "
                "in the Thresholds tab to apply the new table to old digs."
            )
        rpm_table = rpm_source.load_table()
        edited = st.data_editor(
            rpm_table,
            num_rows="dynamic",
            width="stretch",
            height=420,
            column_config={
                "market": st.column_config.SelectboxColumn("Market", options=list(dict.fromkeys([*cfg.ENABLED_MARKETS, *rpm_table["market"]])), required=True,
                    help="New rows only for markets enabled in the Markets tab"),
                "category": st.column_config.TextColumn("Topic category", required=True),
                "rpm_low": st.column_config.NumberColumn("RPM low", min_value=0.0, format="$%.2f", required=True),
                "rpm_high": st.column_config.NumberColumn("RPM high", min_value=0.0, format="$%.2f", required=True),
            },
            key="rpm_editor",
        )
        c1, c2, _ = st.columns([1, 1.4, 3])
        if c1.button("Save RPM table", type="primary", width="stretch"):
            bad = edited[edited["rpm_low"] > edited["rpm_high"]]
            if not bad.empty:
                st.error(f"{len(bad)} rows have RPM low above RPM high.")
            else:
                rpm_source.save_table(edited)
                st.success("Saved. Press \"Recompute all digs\" in the Thresholds tab to apply it to old digs.")
        if c2.button("Restore default table", width="stretch"):
            rpm_source.save_table(rpm_source.default_table())
            st.rerun()

# ---------------------------------------------------------------------------
WEIGHT_NAMES = {
    "demand": "Demand", "opportunity": "Opportunity", "freshness": "Freshness", "competition": "Competition",
    "big_channels": "Big channels", "exact_title": "Exact-match titles",
    "momentum": "Momentum", "durability": "30-day steadiness", "youtube": "YouTube", "persistence": "Days repeated",
}

with tab_mk:
    cat = mc.catalog(conn)
    stored = {m["region"]: m for m in mc.list_markets(conn)}
    n_on = sum(1 for m in stored.values() if m["enabled"])
    with st.container(border=True):
        theme.section("Markets", "The one list every page uses: Dig, Sub-niches & keywords, Compare, Keyword radar and the RPM table only offer markets enabled here.")
        with st.expander("How markets work"):
            st.markdown(
                "Regions and languages follow Google's official lists (YouTube i18nRegions / i18nLanguages). The language is the "
                "search language (relevanceLanguage) used for that market. Disabling a market keeps its old data. "
                "The group is used on the Compare page; leave it blank for \"Other\"."
            )
        k1, k2 = st.columns([3, 2])
        k1.caption(
            f"Google list: {len(cat['regions'])} regions, {len(cat['languages'])} languages, loaded {cat['fetched_at'][:10]}. {n_on} markets enabled."
            if cat else f"Google's list is not loaded yet: the table only shows stored markets. {n_on} markets enabled."
        )
        if k2.button("🔄 Load Google's regions and languages (2 units)", disabled=not cfg.api_key(), width="stretch"):
            try:
                mc.refresh_catalog(conn)
                st.rerun()
            except YouTubeError as e:
                st.error(str(e))

        region_names = {r["code"]: r["name"] for r in (cat or {}).get("regions", [])}
        lang_names = {lang["code"]: lang["name"] for lang in (cat or {}).get("languages", [])}
        for m in stored.values():  # languages in use that the catalog does not list yet
            lang_names.setdefault(m["hl"], m["hl"])
        lang_label = {code: f"{name} ({code})" for code, name in lang_names.items()}
        label_to_hl = {v: k for k, v in lang_label.items()}

        codes = list(stored) + sorted((c for c in region_names if c not in stored), key=lambda c: region_names[c])
        table = pd.DataFrame([
            {
                "On": bool(stored[c]["enabled"]) if c in stored else False,
                "Code": c,
                "Region": stored[c]["name"] if c in stored else region_names[c],
                "Language": lang_label.get(stored[c]["hl"] if c in stored else mc.default_hl(c), mc.default_hl(c)),
                "Group": stored[c]["group_name"] if c in stored else "",
            }
            for c in codes
        ])
        query = st.text_input("Filter by region name or code", placeholder="e.g. Spain, ES")
        if query.strip():
            q = query.strip().lower()
            table = table[table["Code"].str.lower().str.contains(q) | table["Region"].str.lower().str.contains(q)]
        edited = st.data_editor(
            table,
            hide_index=True,
            width="stretch",
            height=460,
            disabled=["Code"],
            column_config={
                "On": st.column_config.CheckboxColumn(width="small"),
                "Language": st.column_config.SelectboxColumn(
                    options=sorted(lang_label.values()), required=True, help="Search language (relevanceLanguage) for this market"
                ),
                "Group": st.column_config.TextColumn(help="Group used on the Compare page; blank = Other"),
            },
            key=f"market_editor_{query}",
        )
        s1, s2, _ = st.columns([1.4, 1.4, 3])
        if s1.button(f"Save markets ({n_on} enabled)", type="primary", width="stretch"):
            try:
                mc.save(conn, [
                    {"region": r["Code"], "name": r["Region"], "hl": label_to_hl.get(r["Language"], r["Language"]),
                     "group_name": r["Group"], "enabled": bool(r["On"])}
                    for r in edited.to_dict("records")
                ])
                st.success("Saved. Every page now uses the new list.")
                st.rerun()
            except mc.MarketError as e:
                st.error(str(e))
        if s2.button("Reset to the 17 default markets", width="stretch"):
            mc.reset_defaults(conn)
            st.rerun()

    if cat:
        with st.expander("Enable markets by language"):
            hl = st.selectbox(
                "Google language", sorted(lang_names, key=lambda c: lang_names[c]), index=None,
                format_func=lambda c: lang_label[c], placeholder="Pick a language",
            )
            if hl:
                lang = mc.relevance_language(hl)
                regions = [c for c in region_names if mc.REGION_LANGUAGE.get(c) == lang]
                st.caption("Regions using this language: " + (", ".join(f"{region_names[c]} ({c})" for c in regions) or "none in the suggestion table; enable them by hand above."))
                if st.button("Enable these regions", disabled=not regions):
                    added = mc.enable_language(conn, hl)
                    st.success(f"Enabled {len(added)} more markets.")
                    st.rerun()

with tab_thr:
    with st.container(border=True):
        theme.section("Thresholds", "Values from the course keep their defaults; the rest are the app's own assumptions. Adjust them from experience.")
        values: dict = {}
        groups = dict.fromkeys(s.group for s in settings.EDITABLE)
        for group in groups:
            with st.expander(group, expanded=False):
                for s in (s for s in settings.EDITABLE if s.group == group):
                    current = getattr(cfg, s.key)
                    default = settings.DEFAULTS[s.key]
                    note = f"Default: {default}" + (f". {s.help}" if s.help else "")
                    if s.kind == "int":
                        values[s.key] = st.number_input(s.label, value=int(current), step=1, help=note, key=f"thr_{s.key}")
                    elif s.kind == "float":
                        values[s.key] = st.number_input(s.label, value=float(current), step=0.05, format="%.2f", help=note, key=f"thr_{s.key}")
                    elif s.kind == "pair":
                        st.markdown(s.label, help=note)
                        p1, p2 = st.columns(2)
                        values[s.key] = (
                            p1.number_input("0 points", value=float(current[0]), step=0.5, key=f"thr_{s.key}_0"),
                            p2.number_input("100 points", value=float(current[1]), step=0.5, key=f"thr_{s.key}_1"),
                        )
                    else:  # weights
                        st.markdown(s.label, help=note)
                        cols = st.columns(len(current))
                        values[s.key] = {
                            k: col.number_input(
                                WEIGHT_NAMES.get(k, k), value=float(current[k]), step=0.05, format="%.2f", key=f"thr_{s.key}_{k}"
                            )
                            for col, k in zip(cols, current)
                        }

        c1, c2, c3, _ = st.columns([1, 1.2, 2, 1.5])
        if c1.button("Save thresholds", type="primary", width="stretch"):
            errors = settings.validate(values)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                settings.save(values)
                st.success("Saved. New digs use the new thresholds right away.")
        if c2.button("Restore defaults", width="stretch"):
            settings.save({})
            for k in list(st.session_state):
                if k.startswith("thr_"):
                    del st.session_state[k]
            st.rerun()
        if c3.button("Recompute all digs (no quota)", width="stretch"):
            runs = run_repo.list_all(conn)
            with st.spinner(f"Recomputing {len(runs)} digs…"):
                for r in runs:
                    reanalyze(conn, r["id"], category=r["category"], affiliates=r["affiliates"])
            st.success(f"Recomputed {len(runs)} digs with the current thresholds and RPM table.")
