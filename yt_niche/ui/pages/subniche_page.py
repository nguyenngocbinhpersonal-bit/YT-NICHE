"""Trang Ngách con & từ khoá dài (bài 2.3): đi hẹp từng tầng, kiểm tra "đủ sống", chấm từ khoá dài."""

import asyncio

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import keyword_score as ks
from yt_niche.core import subniche as sn
from yt_niche.db import runs as run_repo
from yt_niche.services import ai
from yt_niche.services import keywords as kw_svc
from yt_niche.services import report
from yt_niche.services import subniches as sub_svc
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources.trends import compare_trends as compare_trends_source
from yt_niche.sources.trends import describe_errors as describe_trends_errors
from yt_niche.ui.common import EST_UNITS_PER_DIG, get_conn, handle_youtube_errors, market_label, run_label, prompt_box

st.title("🌿 Ngách con & từ khoá dài")
st.caption(
    "Đi hẹp từng tầng, mỗi tầng chỉ thêm một bộ lọc rồi đào lại. Hẹp nhưng vẫn phải còn người xem. "
    "10 video đầu nhắm từ khoá dài điểm từ 55."
)
conn = get_conn()

all_runs = run_repo.list_all(conn)
if not all_runs:
    st.info("Chưa có lần đào nào. Vào trang Đào ngách, đào ngách mẹ trước.")
    st.stop()

by_id = {r["id"]: r for r in all_runs}
ids = list(by_id)
current = st.session_state.get("sub_run") or st.session_state.get("current_run")
run_id = st.selectbox(
    "Ngách đang xét (ngách mẹ khi đào sâu)",
    ids,
    index=ids.index(current) if current in ids else 0,
    format_func=lambda i: f"{'· ' * ((by_id[i].get('layer') or 1) - 1)}Tầng {by_id[i].get('layer') or 1} · {run_label(by_id[i])}",
)
st.session_state["sub_run"] = run_id
run = run_repo.get(conn, run_id)
r = run["result"]
language = run["language"]
status = sn.layer_status(run)
FILTER_KEY = {v: k for k, v in sn.FILTERS.items()}
STATUS_ICON = {"alive": "🟢", "weak": "🟡", "too_narrow": "🔴", "video": "🎬"}
st.markdown(f"{STATUS_ICON[status['status']]} **Tầng {run.get('layer') or 1}**: {status['text']}")

tab_sub, tab_tree, tab_kw, tab_prompt = st.tabs(
    ["1. Ngách con", "2. Cây ngách & kiểm tra đủ sống", "3. Từ khoá dài", "4. Prompt"]
)

# ---------------------------------------------------------------------------
# 1. Ngách con: cụm lặp lại ở video thắng + gợi ý tìm kiếm → đào sâu
# ---------------------------------------------------------------------------

with tab_sub:
    phrases = sn.repeated_phrases(r["outliers"], run["topic"])
    st.markdown("**Cụm lặp lại trong tiêu đề video vượt trội** (bằng chứng người xem thích chủ đề đó)")
    if phrases:
        st.dataframe(
            pd.DataFrame([{"Cụm": p["phrase"], "Số video thắng": p["videos"], "Số kênh": p["channels"]} for p in phrases]),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(f"Chưa có cụm nào lặp lại ở từ {cfg.SUBNICHE_MIN_CHANNELS} kênh trở lên.")

    c1, c2 = st.columns([1, 2])
    az = c2.checkbox("Mở rộng A–Z (thêm biến thể, khoảng 25 lượt gọi gợi ý)", key="sub_az")
    if c1.button("🔎 Lấy gợi ý tìm kiếm YouTube", help="Miễn phí, không tốn quota API"):
        with st.spinner("Đang lấy gợi ý…"):
            try:
                got = kw_svc.fetch_suggestions(conn, run["topic"], run["region"], az=az)
                st.success(f"Lấy được {len(got)} gợi ý.")
            except Exception as e:  # mạng lỗi, Google chặn tạm thời…
                st.error(f"Không lấy được gợi ý: {e}")

    saved = kw_svc.list_keywords(conn, run["topic"], run["region"])
    cols = sn.columns(saved, run["topic"], language, r["outliers"])
    if saved:
        st.caption(f"{len(saved)} gợi ý cho \"{run['topic']}\" ở {market_label(run['region'])}.")
        col_sub, col_long, col_short = st.columns(3)
        for col, kind in ((col_sub, sn.SUBNICHE), (col_long, sn.LONG), (col_short, sn.SHORT)):
            col.markdown(f"**{kind}** ({len(cols[kind])})")
            rows = [
                {"Từ khoá": x["keyword"], "Video thắng": x["videos"]} if kind == sn.SUBNICHE else {"Từ khoá": x["keyword"]}
                for x in cols[kind]
            ]
            if rows:
                col.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300)
        st.caption(
            "Từ khoá ngắn: lượng tìm lớn, cạnh tranh cao, dùng làm từ khoá phụ trong mô tả. "
            "Từ khoá dài: chấm ở tab 3. Cột Ngách con: ứng viên tầng tiếp theo, ưu tiên cụm có video thắng."
        )

    st.markdown("**Đào sâu: tối đa 3 ngách con**")
    if (run.get("layer") or 1) >= cfg.SUBNICHE_MAX_LAYER:
        st.warning(
            f"Ngách đang xét đã ở tầng {run.get('layer') or 1}. Tầng {cfg.SUBNICHE_MAX_LAYER + 1} trở xuống thường là chủ đề video, "
            "không phải ngách: dùng để lập danh sách video, đừng dựng cả kênh trên đó."
        )
    if status["status"] == "too_narrow":
        st.error("Ngách đang xét đã quá hẹp. Đừng xuống tầng nữa; hãy chọn ngách mẹ của nó và thử bộ lọc khác.")
    candidates = [x["keyword"] for x in cols[sn.SUBNICHE][:3]]
    editor = st.data_editor(
        pd.DataFrame(
            {
                "Đào": [True] * len(candidates) + [False] * (3 - len(candidates)),
                "Ngách con": candidates + [""] * (3 - len(candidates)),
                "Bộ lọc thêm": [sn.FILTERS["problem"]] * 3,
            }
        ),
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            "Bộ lọc thêm": st.column_config.SelectboxColumn(
                options=list(sn.FILTERS.values()), required=True,
                help="Mỗi tầng chỉ thêm MỘT bộ lọc: " + "; ".join(f"{sn.FILTERS[k]}: {v}" for k, v in sn.FILTER_EXAMPLES.items()),
            ),
        },
        key=f"deep_{run_id}",
    )
    todo = [row for _, row in editor.iterrows() if row["Đào"] and str(row["Ngách con"]).strip()]
    st.caption(
        f"Cùng thị trường, khoảng thời gian, định dạng với ngách đang xét. "
        f"Mỗi ngách tối đa khoảng {EST_UNITS_PER_DIG} unit → {len(todo) * EST_UNITS_PER_DIG} unit."
    )
    if st.button(f"⛏️ Đào sâu {len(todo)} ngách con", type="primary", disabled=not todo or not cfg.api_key()):
        params = r["params"]
        for row in todo:
            topic = " ".join(str(row["Ngách con"]).split())
            with st.spinner(f"Đang đào \"{topic}\"…"):
                new_id = handle_youtube_errors(
                    dig,
                    conn,
                    DigParams(**{**params, "topic": topic}),
                    category=run["category"],
                    affiliates=run["affiliates"],
                    parent_id=run_id,
                    filter_type=FILTER_KEY[row["Bộ lọc thêm"]],
                )
            if new_id is None:
                break
        st.rerun()

# ---------------------------------------------------------------------------
# 2. Cây ngách và phiếu "đủ sống"
# ---------------------------------------------------------------------------

with tab_tree:
    root = sub_svc.root_of(conn, run_id)
    nodes = sub_svc.tree(conn, root)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Ngách": "　" * n["depth"] + ("└ " if n["depth"] else "") + n["run"]["topic"],
                    "Tầng": n["run"].get("layer") or 1,
                    "Bộ lọc": sn.FILTERS.get(n["run"].get("filter_type"), ""),
                    "Điểm": round(n["run"]["score"]),
                    "Tin cậy": n["run"]["confidence"],
                    "Vượt trội": n["run"]["result"]["outlier_counts"]["total"],
                    "Kênh nhỏ nổ": len(n["run"]["result"]["small_boom_channels"]),
                    "Trạng thái": f"{STATUS_ICON[n['status']['status']]} {n['status']['text']}",
                    "#": n["run"]["id"],
                }
                for n in nodes
            ]
        ),
        hide_index=True,
        width="stretch",
        column_config={"Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
    )
    if len(nodes) == 1:
        st.caption("Chưa có ngách con nào. Đào sâu ở tab 1.")
    else:
        with st.expander("📈 So Google Trends ngách mẹ với tối đa 4 ngách con (cùng một thang)"):
            root_run = nodes[0]["run"]
            kids = list(dict.fromkeys(n["run"]["topic"] for n in nodes[1:] if n["run"]["topic"] != root_run["topic"]))
            chosen = st.multiselect("Ngách con", kids, default=kids[:4], max_selections=4, key=f"trcmp_{root}")
            if st.button("So Trends", disabled=not chosen, key=f"trcmp_btn_{root}"):
                with st.spinner("Đang lấy Google Trends…"):
                    got = asyncio.run(
                        compare_trends_source(conn, [root_run["topic"], *chosen], root_run["region"])
                    )
                st.session_state[f"trcmp_{root}_res"] = got
            got = st.session_state.get(f"trcmp_{root}_res")
            if got:
                if got["errors"]:
                    st.warning(describe_trends_errors(got["errors"]))
                rows = sn.compare_trends(got["series"], root_run["topic"])
                if rows:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Chủ đề": ("🌳 " if x["is_parent"] else "") + x["topic"],
                                    "12 tháng": x["direction_12m"],
                                    "5 năm": x["direction_5y"],
                                    "Mức 13 tuần gần nhất": x["level"],
                                    "Ứng viên tốt": "⭐" if x["good_candidate"] else "",
                                }
                                for x in rows
                            ]
                        ),
                        hide_index=True,
                        width="stretch",
                    )
                    points = (got["series"].get("12m") or {})
                    if any(points.values()):
                        frames = {t: pd.Series({d: v for d, v in pts}) for t, pts in points.items() if pts}
                        st.line_chart(pd.DataFrame(frames), height=240)
                    st.caption("Ngách con đi lên trong khi ngách mẹ đi ngang hoặc giảm là ứng viên tốt (⭐).")

    st.markdown(f"**Phiếu kiểm tra \"đủ sống\": {run['topic']}**")
    check = sub_svc.check(conn, run_id)
    if ai.enabled(conn, "prompts"):
        if st.button(
            "🤖 AI điền lời hứa và 30 ý tưởng", key=f"ai_fill_{run_id}",
            help="Dựa trên kết quả đào, từ khoá dài đã chấm và video vượt trội. Lời hứa đã viết thì giữ nguyên; "
                 "ô ý tưởng được thay bằng kết quả AI. Tốn 1 lượt AI.",
        ):
            with st.spinner("AI đang viết lời hứa và ý tưởng…"):
                try:
                    prompt = report.build_ideas_prompt(
                        run, check["promise"], kw_svc.scored_for(conn, run["topic"], run["region"]),
                        st.session_state.get(f"sub_style_{run_id}", ""),
                    )
                    got = ai.fill_viability(conn, prompt, language, check["promise"])
                except ai.AIError as e:
                    st.error(str(e))
                else:
                    sub_svc.save_check(conn, run_id, ideas="\n".join(got["ideas"]), promise=got["promise"])
                    st.rerun()
    else:
        st.caption("Bật AI (Cài đặt → AI, tính năng prompt) để AI tự điền lời hứa kênh và 30 ý tưởng.")
    with st.form(f"check_{run_id}"):
        promise = st.text_input(
            "Lời hứa kênh một câu",
            value=check["promise"],
            placeholder="Kênh giúp [ai] hiểu / làm được [điều gì]",
        )
        ideas = st.text_area(
            f"{cfg.SUBNICHE_MIN_IDEAS} ý tưởng video (mỗi dòng một ý; có thể dán kết quả prompt \"Kiểm tra 30 ý tưởng\")",
            value=check["ideas"],
            height=220,
        )
        if st.form_submit_button("Lưu và kiểm tra"):
            sub_svc.save_check(conn, run_id, ideas=ideas, promise=promise)
            st.rerun()

    for c in check["checks"]:
        icon = "✅" if c["ok"] else "⬜" if c["ok"] is None else "❌"
        st.markdown(f"{icon} **{c['label']}**: {c['detail']}")
    dups = sn.duplicate_ideas(sn.parse_ideas(check["ideas"]))
    if dups:
        ideas_list = sn.parse_ideas(check["ideas"])
        with st.expander(f"{len(dups)} cặp ý trùng / gần trùng nên gộp"):
            for i, j in dups[:30]:
                st.markdown(f"- {ideas_list[i]} ↔ {ideas_list[j]}")
    if check["passed"]:
        st.success("Ngách con đủ sống. Đi tiếp: so với các ngách khác (trang So sánh) rồi chấm trọng số (Bảng chấm).")
    elif check["checks"][2]["ok"] is False:
        st.warning("Chưa đủ 30 ý không trùng: ngách có thể quá hẹp, thử nới một bộ lọc.")

# ---------------------------------------------------------------------------
# 3. Từ khoá dài
# ---------------------------------------------------------------------------

with tab_kw:
    k1, k2 = st.columns([2, 1])
    seed = k1.text_input("Ngách con tầng 2 để lấy gợi ý", value=run["topic"], key=f"seed_{run_id}")
    regions = list(dict.fromkeys([run["region"], *cfg.ENABLED_MARKETS]))  # giữ thị trường của lần đào dù đã tắt
    region = k2.selectbox("Thị trường", regions, index=0, format_func=market_label, key=f"kwr_{run_id}")
    seed_norm = " ".join(seed.lower().split())

    b1, b2 = st.columns([1, 2])
    az_kw = b2.checkbox("Mở rộng A–Z", key=f"kwaz_{run_id}")
    if b1.button("🔎 Lấy gợi ý", key=f"kwget_{run_id}"):
        try:
            kw_svc.fetch_suggestions(conn, seed_norm, region, az=az_kw)
        except Exception as e:
            st.error(f"Không lấy được gợi ý: {e}")
    with st.expander("Thêm từ khoá thủ công"):
        manual = st.text_area("Mỗi dòng một từ khoá", key=f"kwman_{run_id}")
        if st.button("Thêm", key=f"kwadd_{run_id}"):
            kw_svc.add_manual(conn, seed_norm, region, manual.splitlines())
            st.rerun()

    kws = kw_svc.list_keywords(conn, seed_norm, region)
    if not kws:
        st.info("Chưa có từ khoá cho ngách này. Bấm \"Lấy gợi ý\".")
    else:
        lang = cfg.MARKETS[region][0]
        good = [k for k in kws if k["score"] is not None and k["score"] >= cfg.KEYWORD_GOOD and not k["low_data"]]
        st.progress(min(1.0, len(good) / 10), text=f"Từ khoá dài đạt ≥ {cfg.KEYWORD_GOOD}, không \"ít dữ liệu\": **{len(good)}/10**")
        df = pd.DataFrame(
            [
                {
                    "Chấm": False,
                    "Từ khoá": k["keyword"],
                    "Loại": sn.keyword_kind(k["keyword"], seed_norm, lang),
                    "Điểm": k["score"],
                    "Nhãn": (ks.label(k["score"]) or "") + (f" · {ks.LOW_DATA}" if k["low_data"] else ""),
                    "Nhu cầu": k["demand"],
                    "Cạnh tranh": k["competition"],
                    "Số kết quả": k["n"],
                    "Chọn đào": k["picked"],
                    "Đã đào": f"#{k['run_id']}" if k["run_id"] else "",
                }
                for k in kws
            ]
        )
        edited = st.data_editor(
            df,
            hide_index=True,
            width="stretch",
            disabled=["Từ khoá", "Loại", "Điểm", "Nhãn", "Nhu cầu", "Cạnh tranh", "Số kết quả", "Đã đào"],
            column_config={
                "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Chấm": st.column_config.CheckboxColumn(help=f"Tick rồi bấm Chấm (tối đa {kw_svc.MAX_SCORE_BATCH})"),
                "Chọn đào": st.column_config.CheckboxColumn(help="Tick 3–5 từ tốt nhất để đào video vượt trội"),
            },
            key=f"kwed_{run_id}_{region}_{seed_norm}",
        )
        to_score = list(edited.loc[edited["Chấm"], "Từ khoá"])
        if not to_score:  # chưa tick: chấm 10 từ khoá dài chưa chấm đầu tiên
            to_score = [
                k["keyword"] for k in kws if k["score"] is None and sn.keyword_kind(k["keyword"], seed_norm, lang) == sn.LONG
            ][: kw_svc.MAX_SCORE_BATCH]
        picked = list(edited.loc[edited["Chọn đào"], "Từ khoá"])

        a1, a2, a3 = st.columns(3)
        if a1.button(
            f"📊 Chấm {len(to_score)} từ (≈ {len(to_score) * kw_svc.UNITS_PER_KEYWORD} unit)",
            disabled=not to_score or not cfg.api_key(),
            key=f"kwscore_{run_id}",
        ):
            if len(to_score) > kw_svc.MAX_SCORE_BATCH:
                st.error(f"Mỗi lần chấm tối đa {kw_svc.MAX_SCORE_BATCH} từ.")
            else:
                with st.spinner("Đang chấm từ khoá trên kết quả YouTube thật…"):
                    if handle_youtube_errors(kw_svc.score, conn, region, to_score) is not None:
                        st.rerun()
        if a2.button("💾 Lưu cột Chọn đào", key=f"kwpick_{run_id}"):
            kw_svc.set_picked(conn, region, [k["keyword"] for k in kws], False)
            kw_svc.set_picked(conn, region, picked, True)
            st.rerun()
        dig_list = [k for k in kws if k["picked"] and not k["run_id"]]
        if a3.button(
            f"⛏️ Đào {len(dig_list)} từ đã chọn (≈ {len(dig_list) * EST_UNITS_PER_DIG} unit)",
            disabled=not dig_list or len(dig_list) > kw_svc.MAX_DIG_PICKED or not cfg.api_key(),
            help=f"Lưu cột Chọn đào trước; tối đa {kw_svc.MAX_DIG_PICKED} từ",
            key=f"kwdig_{run_id}",
        ):
            for k in dig_list:
                with st.spinner(f"Đang đào \"{k['keyword']}\"…"):
                    params = DigParams(**{**r["params"], "topic": k["keyword"], "region": region, "language": cfg.MARKETS[region][0]})
                    new_id = handle_youtube_errors(dig, conn, params, category=run["category"])
                if new_id is None:
                    break
                kw_svc.link_run(conn, region, k["keyword"], new_id)
            st.rerun()
        if any(k["low_data"] for k in kws if k["score"] is not None):
            st.caption(f"Từ mang nhãn \"{ks.LOW_DATA}\": chấm lại hoặc kiểm tra bằng Đào ngách trước khi dùng.")
        st.caption(
            "Điểm từ khoá (giả định của app): 50% Nhu cầu (trung vị lượt xem kết quả, 100 → 0 điểm, 100K → 100 điểm) "
            f"+ 50% (100 − Cạnh tranh). Cạnh tranh = % kết quả từ kênh ≥ {cfg.KEYWORD_BIG_CHANNEL_SUBS:,} sub và % tiêu đề "
            f"nhắm đúng từ khoá. Ngưỡng: ≥ {cfg.KEYWORD_VERY_GOOD} Rất tốt · {cfg.KEYWORD_GOOD}–{cfg.KEYWORD_VERY_GOOD - 1} Tốt · "
            f"{cfg.KEYWORD_AVERAGE}–{cfg.KEYWORD_GOOD - 1} Trung bình.".replace(",", ".")
        )

# ---------------------------------------------------------------------------
# 4. Prompt
# ---------------------------------------------------------------------------

with tab_prompt:
    style = st.text_input("Kiểu kênh dự định", placeholder="vd. không lộ mặt, giọng AI", key=f"sub_style_{run_id}")
    st.markdown("**Bung ngách con theo 4 bộ lọc**")
    prompt_box(report.build_subniche_prompt(run, cols, phrases, style))
    st.markdown("**Kiểm tra 30 ý tưởng cho một ngách con**")
    check = sub_svc.get_check(conn, run_id)
    prompt_box(report.build_ideas_prompt(run, check["promise"], kw_svc.scored_for(conn, run["topic"], run["region"]), style))
