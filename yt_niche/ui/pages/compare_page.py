"""Trang So sánh (bài 2.4): so 2–5 ngách bằng bảng 7 cột và đào đa thị trường."""

import asyncio

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import compare as cmp_core
from yt_niche.db import runs as run_repo
from yt_niche.services import ai, markets as mk_svc
from yt_niche.services import report
from yt_niche.services import scorecards as sc_svc
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.suggest import suggestions
from yt_niche.ui.common import NO_CATEGORY, fmt_num, get_conn, market_label, render_run, run_label, prompt_box

st.title("🌍 So sánh ngách và thị trường")
st.caption(
    "Chỉ so các phương án đào cùng thiết lập, cùng ngày. Chênh từ 10 điểm là rõ; dưới 5 điểm coi như ngang; "
    "2–3 điểm là nhiễu."
)
conn = get_conn()
BEST = " 🟩"

tab_niche, tab_market, tab_prompt = st.tabs(["So ngách (7 cột)", "Đa thị trường", "Prompt"])

# ---------------------------------------------------------------------------
# So ngách
# ---------------------------------------------------------------------------

with tab_niche:
    all_runs = run_repo.list_all(conn)
    by_id = {r["id"]: r for r in all_runs}
    if len(all_runs) < 2:
        st.info("Cần ít nhất 2 lần đào. Vào trang Đào ngách hoặc Ngách con để đào thêm.")
        cmp, picked_runs, ability_text = None, [], ""
    else:
        default = [r["id"] for r in all_runs if r["shortlisted"]][:5] or [r["id"] for r in all_runs[:2]]
        picked = st.multiselect(
            "Chọn 2–5 ngách (nên là ngách con đã đào cùng thiết lập, cùng ngày)",
            list(by_id),
            default=default,
            format_func=lambda i: run_label(by_id[i]),
            max_selections=5,
        )
        ability = st.radio(
            "Định dạng bạn làm được",
            list(cmp_core.ABILITY),
            format_func=cmp_core.ABILITY.get,
            horizontal=True,
            index=2,
        )
        ability_text = st.text_input(
            "Năng lực / hoàn cảnh (đưa vào prompt)", placeholder="vd. không lộ mặt, 2 video/tuần, viết được tiếng Anh"
        )
        picked_runs = [by_id[i] for i in picked]
        cmp = cmp_core.compare(picked_runs, ability) if len(picked_runs) >= 2 else None

    if cmp:
        for w in cmp["warnings"]:
            st.warning(f"⚠️ {w}")
        for r in cmp["excluded"]:
            st.error(f"Loại khỏi xếp hạng: {r['name']} (độ tin cậy thấp, {r['n']} video). Hãy đào lại cho đủ mẫu.")

        best = cmp["best"]

        def mark(rid: int, col: str, text: str) -> str:
            return text + (BEST if rid in best[col] else "")

        rows = []
        for r in cmp["rows"]:
            income = f"{r['income'][0]:g}–{r['income'][1]:g} USD" if r["income"] else "chưa có RPM"
            trend = r["trend_class"] or "chưa có"
            if r["trend_class"]:
                trend += f" (1 năm {r['trend_12m']:+.0%}, 5 năm {r['trend_5y']:+.0%})"
            fit = report.FMT_LABEL.get(r["format_fit"], "chưa rõ") if r["format_fit"] else "chưa rõ"
            rows.append(
                {
                    "Ngách": r["name"],
                    "Điểm + nhãn": mark(r["id"], "score", f"{r['score']:.0f} · {r['label']}"),
                    "Độ tin cậy": mark(r["id"], "confidence", f"{r['confidence']} ({r['n']})"),
                    "Lượt xem trung vị": mark(r["id"], "median_views", fmt_num(r["median_views"])),
                    "Vượt trội / kênh nhỏ nổ": mark(r["id"], "doors", f"{r['outliers']} / {r['small_channels']}"),
                    "Trends": mark(r["id"], "trends", trend),
                    "Thu nhập/video (≈)": mark(r["id"], "income", income),
                    "Định dạng thắng": mark(r["id"], "format", fit),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption(f"{BEST.strip()} ô tốt nhất mỗi cột. Định dạng: tô xanh khi hợp định dạng bạn làm được.")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Chênh điểm từng cặp**")
            if cmp["pairs"]:
                st.dataframe(
                    pd.DataFrame([{"Cặp": f"{p['a']} – {p['b']}", "Chênh": p["gap"], "Đọc là": p["verdict"]} for p in cmp["pairs"]]),
                    hide_index=True,
                    width="stretch",
                )
        with c2:
            if cmp["view_ratio"]:
                st.metric("Lượt xem trung vị: cao nhất / thấp nhất", f"{cmp['view_ratio']:g}×")
            for name in cmp["big_channel_niches"]:
                st.caption(f"⚠️ {name}: điểm khá nhưng ít kênh nhỏ nổ, có thể là ngách của kênh lớn.")

        if cmp["leader"]:
            with st.container(border=True):
                st.markdown("**Kết luận 3 câu** (app gợi ý, bạn sửa lại bằng lời mình)")
                st.markdown(
                    f"1. **{cmp['leader']['name']}** dẫn đầu: {cmp['leader_reason']}.\n"
                    f"2. Cột tạo khác biệt: {cmp['differentiator']}.\n"
                    f"3. Rủi ro lớn nhất: {cmp['risk']}."
                )
            for d in cmp["doors"]:
                st.caption(f"🚪 Ngõ của ngách thua: {d}. Đừng loại mà không mở khung kênh nổ của nó.")

            with st.form("cmp_board"):
                name = st.text_input("Tên bảng chấm", value="So ngách " + pd.Timestamp.now().strftime("%d/%m"))
                if st.form_submit_button("⚖️ Tạo bảng chấm từ các ngách này"):
                    ids = [r["id"] for r in cmp["rows"]]
                    try:
                        st.session_state["board"] = sc_svc.create(conn, name, ids)
                        st.success("Đã tạo bảng chấm. Vào trang Bảng chấm để khoá trọng số và chấm điểm.")
                    except sc_svc.ScorecardError as e:
                        st.error(str(e))

# ---------------------------------------------------------------------------
# Đa thị trường
# ---------------------------------------------------------------------------

with tab_market:
    batches = mk_svc.list_batches(conn)
    NEW = 0
    options = [NEW] + [b["id"] for b in batches]
    labels = {NEW: "➕ Lần đào mới"} | {
        b["id"]: f"{b['topic']} · {b['n_done'] or 0}/{b['n']} nước · {b['created_at'][:10]} (#{b['id']})" for b in batches
    }
    current = st.session_state.get("batch")
    batch_id = st.selectbox(
        "Lần đào đa thị trường", options, index=options.index(current) if current in options else (1 if batches else 0),
        format_func=labels.get,
    )
    if batch_id != NEW:
        st.session_state["batch"] = batch_id

    if batch_id == NEW:
        topic = st.text_input("Chủ đề gốc (tiếng Việt)", placeholder="cầu nguyện buổi sáng", key="mk_topic")
        g = st.columns(len(cfg.MARKET_GROUPS))
        groups = [name for col, name in zip(g, cfg.MARKET_GROUPS) if col.checkbox(name, key=f"grp_{name}")]
        from_groups = [code for name in groups for code in cfg.MARKET_GROUPS[name]]
        regions = st.multiselect(
            f"Thị trường (6–10 nước, tối đa {mk_svc.MAX_MARKETS})",
            cfg.ENABLED_MARKETS,
            default=[m for m in dict.fromkeys(["VN"] + from_groups) if m in cfg.ENABLED_MARKETS][: mk_svc.MAX_MARKETS],
            format_func=market_label,
            max_selections=mk_svc.MAX_MARKETS,
            key=f"mk_regions_{'_'.join(groups)}",
        )

        with st.expander("Dịch từ khoá: copy prompt sang Claude / ChatGPT rồi dán kết quả vào đây", expanded=bool(topic)):
            st.caption("Đừng dịch từng chữ: cần cụm người bản xứ thật sự gõ trên YouTube.")
            if topic and regions:
                tr_prompt = report.build_translate_prompt(topic, [r for r in regions if r != "VN"] or regions)
                st.code(tr_prompt, language="markdown")
                if ai.enabled(conn, "prompts") and st.button("🤖 Dịch bằng AI (điền sẵn ô dưới)", key="mk_ai_translate"):
                    with st.spinner("AI đang dịch…"):
                        try:
                            answer = ai.run_prompt(conn, tr_prompt, tier="fast").strip().strip("`").strip()
                            if report.parse_translations(answer):
                                st.session_state["mk_paste"] = answer
                            else:
                                st.error(f"AI trả lời không đúng dạng \"US: từ khoá\": {answer[:200]}")
                        except ai.AIError as e:
                            st.error(str(e))
            pasted = st.text_area("Dán kết quả (mỗi dòng \"US: từ khoá\")", key="mk_paste", height=120)
        translations = report.parse_translations(pasted)
        if topic:
            translations.setdefault("VN", topic)

        editor = st.data_editor(
            pd.DataFrame({"Thị trường": regions, "Từ khoá": [translations.get(r, "") for r in regions]}),
            hide_index=True,
            width="stretch",
            disabled=["Thị trường"],
            key=f"mk_kw_{hash((tuple(regions), pasted, topic))}",
        )
        keywords = {row["Thị trường"]: str(row["Từ khoá"] or "") for _, row in editor.iterrows()}

        if st.button("🔎 Kiểm tra từ khoá có người gõ không (gợi ý YouTube, miễn phí)", disabled=not any(keywords.values())):

            async def _check():
                return await asyncio.gather(
                    *(suggestions(conn, kw, r, cfg.MARKETS[r][0]) for r, kw in keywords.items() if kw.strip())
                )

            try:
                results = asyncio.run(_check())
                for (r, kw), sug in zip([(r, kw) for r, kw in keywords.items() if kw.strip()], results):
                    top = ", ".join(s["keyword"] for s in sug[:4]) or "không có gợi ý nào"
                    icon = "🟢" if sug else "🔴"
                    st.markdown(f"{icon} **{market_label(r)}** \"{kw}\": {top}")
            except Exception as e:
                st.error(f"Không lấy được gợi ý: {e}")

        m1, m2, m3, m4 = st.columns(4)
        category = m1.selectbox("Nhóm chủ đề (RPM)", [NO_CATEGORY] + rpm_source.categories(), key="mk_cat")
        window_days = m2.selectbox("Khoảng thời gian", [7, 30, 90, 365], index=1, format_func=lambda d: f"{d} ngày", key="mk_win")
        fmt = m3.selectbox("Định dạng", list(report.FMT_LABEL), format_func=report.FMT_LABEL.get, key="mk_fmt")
        pages = m4.selectbox("Số trang search", [1, 2], key="mk_pages", help="1 trang ≈ 200 unit/nước, 2 trang ≈ 320")
        with_trends = st.checkbox(
            "Kèm Google Trends từng nước (dễ bị giới hạn 429 khi nhiều nước; không có thì dùng công thức 60/40)",
            key="mk_trends",
        )
        n = sum(1 for k in keywords.values() if k.strip())
        if category == NO_CATEGORY:
            st.warning("Chọn nhóm chủ đề để có RPM; không có RPM thì không tính được Điểm cơ hội.")
        st.caption(f"Ước tính {mk_svc.estimate_units(n, pages):,} unit cho {n} nước (ít hơn nếu kênh đã có trong cache).".replace(",", "."))
        if st.button(f"⛏️ Đào {n} thị trường", type="primary", disabled=n < 2 or not cfg.api_key()):
            try:
                bid = mk_svc.create_batch(
                    conn, topic, keywords, category=None if category == NO_CATEGORY else category,
                    window_days=window_days, fmt=fmt,
                )
            except mk_svc.MarketError as e:
                st.error(str(e))
            else:
                bar = st.progress(0.0, text="Bắt đầu…")
                out = mk_svc.run_batch(
                    conn, bid, pages=pages, with_trends=with_trends,
                    on_progress=lambda i, total, r: bar.progress(i / total, text=f"Đang đào {market_label(r)} ({i + 1}/{total})"),
                )
                if out["stopped"]:
                    st.error(out["stopped"])
                st.session_state["batch"] = bid
                st.rerun()
    else:
        batch = mk_svc.load_batch(conn, batch_id)
        st.caption(
            f"Chủ đề gốc \"{batch['topic']}\" · {report.FMT_LABEL[batch['fmt']]} · {batch['window_days']} ngày · "
            f"nhóm {batch['category'] or 'chưa chọn'} · đào ngày {batch['created_at'][:10]}"
        )
        done = [it for it in batch["items"] if it.get("result")]
        rows = []
        for it in done:
            r, o = it["result"], it["opportunity"]
            ch = it["sample_channel"]
            rows.append(
                {
                    "Thị trường": market_label(it["region"]),
                    "Từ khoá": it["keyword"],
                    "Điểm cơ hội": o["score"],
                    "Vùng": ("🟢 xanh" if o.get("green") else "") if o["score"] is not None else o["formula"],
                    "Điểm ngách": round(r["score"]),
                    "Tin cậy": r["confidence"],
                    "Lượt xem trung vị": r["median_views"],
                    "Kênh nhỏ nổ": len(r["small_boom_channels"]),
                    "RPM (≈ USD)": f"{r['rpm'][0]:g}–{r['rpm'][1]:g}" if r.get("rpm") else "",
                    "Thu nhập/video (≈ USD)": f"{r['income_per_video'][0]:g}–{r['income_per_video'][1]:g}" if r.get("income_per_video") else "",
                    "Chủ đề con thắng": ", ".join(it["topics"][:3]),
                    "Kênh mẫu": (
                        f"{ch['title']} · {ch['age_months']} tháng · {fmt_num(ch['subs'])} sub · {fmt_num(ch['best_views'])} view"
                        if ch
                        else ""
                    ),
                }
            )
        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                hide_index=True,
                width="stretch",
                column_config={
                    "Điểm cơ hội": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                    "Lượt xem trung vị": st.column_config.NumberColumn(format="localized"),
                },
            )
            formulas = {it["opportunity"]["formula"] for it in done if it["opportunity"]["score"] is not None}
            st.caption(
                f"Điểm cơ hội = {' | '.join(sorted(formulas))}. Từ {cfg.OPPORTUNITY_GREEN_MIN} trở lên là vùng xanh. "
                "Điểm cơ hội cao không có nghĩa là bạn làm được: chấm thêm Sản xuất bền ở Bảng chấm. "
                "Thu nhập ước tính chỉ so tương đối, không phải số để tính lương."
            )
        missing = [it for it in batch["items"] if not it.get("result")]
        for it in missing:
            st.caption(f"⏳ {market_label(it['region'])} \"{it['keyword']}\": {it['error'] or 'chưa đào'}")
        b1, b2, _ = st.columns([1, 1, 2])
        if missing and b1.button(f"Đào tiếp {len(missing)} nước còn thiếu", disabled=not cfg.api_key()):
            out = mk_svc.run_batch(conn, batch_id)
            if out["stopped"]:
                st.error(out["stopped"])
            st.rerun()
        if b2.button("🗑️ Xoá lần đào đa thị trường này"):
            mk_svc.delete_batch(conn, batch_id)
            st.session_state.pop("batch", None)
            st.rerun()

        if done:
            detail = st.selectbox(
                "Xem chi tiết một nước (chủ đề con, video thắng, kênh nổ)",
                [None] + [it["run_id"] for it in done],
                format_func=lambda i: "—" if i is None else next(market_label(it["region"]) for it in done if it["run_id"] == i),
            )
            if detail:
                st.divider()
                render_run(run_repo.get(conn, detail, with_raw=True))

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

with tab_prompt:
    st.markdown("**So 2–3 ngách từ bảng số**")
    if cmp:
        prompt_box(report.build_compare_prompt(cmp, picked_runs, ability_text))
    else:
        st.caption("Chọn ít nhất 2 ngách ở tab So ngách.")
    st.markdown("**So nhiều nước từ bảng số**")
    batch_for_prompt = mk_svc.load_batch(conn, st.session_state["batch"]) if st.session_state.get("batch") else None
    if batch_for_prompt and any(it.get("result") for it in batch_for_prompt["items"]):
        prompt_box(report.build_market_compare_prompt(batch_for_prompt, ability_text))
        st.markdown("**Kiểm tra khả năng làm nội dung chuẩn bản xứ**")
        items = [it for it in batch_for_prompt["items"] if it.get("result") and it["region"] != "VN"]
        if items:
            region = st.selectbox("Thị trường", [it["region"] for it in items], format_func=market_label, key="native_r")
            item = next(it for it in items if it["region"] == region)
            prompt_box(report.build_native_prompt(item, batch_for_prompt["topic"]))
    else:
        st.caption("Chọn một lần đào đa thị trường đã có kết quả ở tab Đa thị trường.")
