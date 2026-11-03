"""Trang Cài đặt: API key, AI, Google Trends (DataForSEO dự phòng), bảng RPM, thị trường, ngưỡng tính điểm."""

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
from yt_niche.ui.common import get_conn

st.title("⚙️ Cài đặt")
conn = get_conn()

tab_key, tab_ai, tab_gt, tab_rpm, tab_mk, tab_thr = st.tabs(
    ["YouTube API key", "🤖 AI", "📈 Google Trends", "Bảng RPM", "🌐 Thị trường", "Ngưỡng tính điểm"]
)

# ---------------------------------------------------------------------------
with tab_key:
    key = cfg.api_key()
    if key:
        st.success(f"Đang dùng key: {key[:4]}…{key[-4:]}")
    else:
        st.warning("Chưa có key.")
    st.caption("Google Cloud Console → bật YouTube Data API v3 → Credentials → Create API key. Key được lưu vào file .env.")
    new_key = st.text_input("Key mới", type="password")
    c1, c2 = st.columns([1, 4])
    if c1.button("Lưu key", disabled=not new_key.strip()):
        cfg.save_api_key(new_key)
        st.success("Đã lưu vào .env.")
        st.rerun()
    if c2.button("Kiểm tra key (tốn 1 unit)", disabled=not key):

        async def _check():
            async with YouTubeClient(conn) as yt:
                return await yt.videos(["jNQXAC9IVRw"], ttl=0)  # video đầu tiên trên YouTube

        try:
            asyncio.run(_check())
            st.success("Key hoạt động.")
        except YouTubeError as e:
            st.error(str(e))

# ---------------------------------------------------------------------------
with tab_ai:
    ai_cfg = ai.get_config(conn)
    st.caption(
        "AI qua API chuẩn OpenAI, mặc định Command Code. Chỉ gửi dữ liệu công khai (từ khoá, tiêu đề, số liệu, nội dung "
        "prompt); không gửi email hay API key YouTube. Tắt AI hoặc AI lỗi thì app vẫn chạy như cũ."
    )
    if ai.api_key():
        st.success(f"Đang dùng key: {ai.masked_key()}")
    else:
        st.warning("Chưa có key AI.")
    new_ai_key = st.text_input("Key mới (COMMANDCODE_API_KEY, lưu vào file .env)", type="password", key="ai_new_key")
    k1, k2 = st.columns([1, 4])
    if k1.button("Lưu key AI", disabled=not new_ai_key.strip()):
        ai.save_api_key(new_ai_key)
        st.rerun()
    base_url = st.text_input("Địa chỉ API (base URL)", ai_cfg["base_url"], help=f"Mặc định Command Code: {cfg.AI_BASE_URL}")
    if k2.button("Kiểm tra kết nối và tải danh sách model", disabled=not ai.api_key()):
        try:
            models = ai.list_models(base_url)
            ai.save_config(conn, {"base_url": base_url.strip(), "models": models})
            st.success(f"Kết nối được: {len(models)} model.")
            for tier in ai.TIERS:
                st.session_state.pop(f"ai_model_{tier}", None)
            st.rerun()
        except ai.AIError as e:
            st.error(str(e))

    # Có key mà chưa có danh sách model: tự tải một lần để chọn từ danh sách.
    if ai.api_key() and not ai_cfg["models"] and not st.session_state.get("ai_models_tried"):
        st.session_state["ai_models_tried"] = True
        try:
            ai_cfg["models"] = ai.list_models(ai_cfg["base_url"])
            ai.save_config(conn, {"models": ai_cfg["models"]})
        except ai.AIError as e:
            st.warning(f"Chưa tải được danh sách model: {e}")
    models = ai_cfg["models"]
    picked_models, picked_effort = {}, {}
    for tier in ai.TIERS:
        m_col, e_col = st.columns([3, 1])
        current = ai_cfg[f"model_{tier}"]
        opts = list(dict.fromkeys(([current] if current else []) + models))
        picked_models[tier] = m_col.selectbox(
            f"Model {ai.TIERS[tier].lower()}", opts, index=opts.index(current) if current else None,
            placeholder="Chọn model (hoặc gõ tên)" if opts else "Gõ tên model hoặc bấm \"Kiểm tra kết nối\"",
            accept_new_options=True, key=f"ai_model_{tier}",
        )
        effort = ai_cfg[f"effort_{tier}"]
        picked_effort[tier] = e_col.selectbox(
            "Effort", list(ai.EFFORTS), index=list(ai.EFFORTS).index(effort) if effort in ai.EFFORTS else 0,
            format_func=ai.EFFORTS.get, key=f"ai_effort_{tier}",
            help="Mức suy nghĩ (reasoning_effort) cho model có hỗ trợ: cao thì kỹ hơn nhưng chậm và tốn token hơn. "
                 "Model không hỗ trợ thì app tự bỏ qua.",
        )
    st.caption("Model nhanh nên là loại rẻ (dùng nhiều lần cho Radar); model sâu dùng khi bấm \"Chạy bằng AI\" cho prompt phân tích.")
    feats = {f: st.checkbox(label, ai_cfg["features"].get(f, True), key=f"ai_feat_{f}") for f, label in ai.FEATURES.items()}
    limit = st.number_input("Tối đa lượt gọi AI mỗi ngày", 1, 100000, int(ai_cfg["max_requests_per_day"]),
                            help="Hết lượt thì AI tự nghỉ đến hôm sau, app vẫn chạy bình thường.")
    on = st.toggle("Bật AI", ai_cfg["enabled"])
    u = ai.usage_today(conn)
    st.caption(f"Hôm nay: {u['requests']}/{ai_cfg['max_requests_per_day']} lượt, {u['prompt_tokens']:,} token vào, {u['completion_tokens']:,} token ra.")
    if st.button("Lưu cài đặt AI", type="primary"):
        if on and not (ai.api_key() and picked_models["fast"] and picked_models["deep"]):
            st.error("Cần key và chọn đủ 2 model trước khi bật AI.")
        else:
            ai.save_config(conn, {"enabled": on, "base_url": base_url.strip(), "model_fast": (picked_models["fast"] or "").strip(),
                                  "model_deep": (picked_models["deep"] or "").strip(), "features": feats,
                                  "effort_fast": picked_effort["fast"], "effort_deep": picked_effort["deep"],
                                  "max_requests_per_day": int(limit)})
            st.success("Đã lưu.")
            st.rerun()
    if ai.enabled(conn) and st.button("Thử 1 câu hỏi (tốn 1 lượt)"):
        try:
            st.info(ai.chat(conn, "fast", [{"role": "user", "content": "Trả lời đúng một từ tiếng Việt: thủ đô của Việt Nam?"}], max_tokens=20))
        except ai.AIError as e:
            st.error(str(e))

# ---------------------------------------------------------------------------
with tab_gt:
    st.caption(
        "Google Trends lấy miễn phí qua Google, nhưng Google hay chặn máy (lỗi 429) hàng chục phút. Khi đó app có thể "
        "lấy cùng dữ liệu qua DataForSEO (trả phí theo lượt, khoảng $0.009/lượt; mỗi lượt = 1 chuỗi tối đa 5 từ khoá). "
        "Chỉ dùng khi Google đang chặn. Chỉ gửi từ khoá, mã nước, nguồn Web/YouTube và mã danh mục."
    )
    d_cfg = dfs.get_config(conn)
    if all(dfs.credentials()):
        st.success(f"Đang dùng login DataForSEO: {dfs.masked_login()}")
    else:
        st.warning("Chưa có login DataForSEO. Tạo tài khoản ở dataforseo.com → Dashboard → API Access (login và password API).")
    l_col, p_col = st.columns(2)
    new_login = l_col.text_input("API login", key="dfs_login")
    new_pass = p_col.text_input("API password", type="password", key="dfs_pass")
    b1, b2 = st.columns([1, 4])
    if b1.button("Lưu login", disabled=not (new_login.strip() and new_pass.strip())):
        dfs.save_credentials(new_login, new_pass)
        st.rerun()
    if b2.button("Kiểm tra kết nối (miễn phí)", disabled=not all(dfs.credentials())):
        try:
            st.success(f"Kết nối được. Số dư: ${dfs.account_balance():,.2f}")
        except dfs.BackupError as e:
            st.error(str(e))
    d_feats = {f: st.checkbox(label, True if f == "dig" else d_cfg["radar"], disabled=f == "dig", key=f"dfs_feat_{f}")
               for f, label in dfs.FEATURES.items()}
    d_limit = st.number_input(
        "Tối đa lượt DataForSEO mỗi ngày", 1, 10000, int(d_cfg["max_tasks_per_day"]),
        help="Hết lượt thì chờ Google mở lại như trước. 40 lượt ≈ $0.36/ngày.",
    )
    d_on = st.toggle("Dùng DataForSEO khi Google chặn", d_cfg["enabled"], key="dfs_on")
    du = dfs.usage_today(conn)
    st.caption(f"Hôm nay: {du['tasks']}/{d_cfg['max_tasks_per_day']} lượt, chi phí ${du['cost']:.3f}.")
    if st.button("Lưu cài đặt Google Trends", type="primary"):
        if d_on and not all(dfs.credentials()):
            st.error("Cần lưu login và password DataForSEO trước khi bật.")
        else:
            dfs.save_config(conn, {"enabled": d_on, "radar": d_feats["radar"], "max_tasks_per_day": int(d_limit)})
            st.success("Đã lưu.")
            st.rerun()

# ---------------------------------------------------------------------------
with tab_rpm:
    st.caption(
        "RPM ước tính (USD / 1.000 view) theo thị trường × nhóm chủ đề. Số mặc định là ước tính thô để xếp hạng ngách, "
        "hãy sửa theo số thật của bạn. Thêm dòng để tạo nhóm chủ đề mới."
    )
    rpm_table = rpm_source.load_table()
    edited = st.data_editor(
        rpm_table,
        num_rows="dynamic",
        width="stretch",
        height=420,
        column_config={
            "market": st.column_config.SelectboxColumn("Thị trường", options=list(dict.fromkeys([*cfg.ENABLED_MARKETS, *rpm_table["market"]])), required=True,
                help="Thêm dòng chỉ cho thị trường đang bật ở tab Thị trường"),
            "category": st.column_config.TextColumn("Nhóm chủ đề", required=True),
            "rpm_low": st.column_config.NumberColumn("RPM thấp", min_value=0.0, format="%.2f", required=True),
            "rpm_high": st.column_config.NumberColumn("RPM cao", min_value=0.0, format="%.2f", required=True),
        },
        key="rpm_editor",
    )
    c1, c2 = st.columns([1, 4])
    if c1.button("Lưu bảng RPM", type="primary"):
        bad = edited[edited["rpm_low"] > edited["rpm_high"]]
        if not bad.empty:
            st.error(f"{len(bad)} dòng có RPM thấp > RPM cao.")
        else:
            rpm_source.save_table(edited)
            st.success("Đã lưu. Bấm \"Tính lại tất cả\" ở tab Ngưỡng để áp vào các lần đào cũ.")
    if c2.button("Khôi phục bảng mặc định"):
        rpm_source.save_table(rpm_source.default_table())
        st.rerun()

# ---------------------------------------------------------------------------
WEIGHT_NAMES = {
    "demand": "Nhu cầu", "opportunity": "Cơ hội", "freshness": "Độ mới", "competition": "Cạnh tranh",
    "big_channels": "Kênh lớn", "exact_title": "Tiêu đề nhắm đúng",
    "momentum": "Đà tăng", "durability": "Độ đều 30 ngày", "youtube": "YouTube", "persistence": "Số ngày lặp lại",
}

with tab_mk:
    st.caption(
        "Nơi duy nhất quản lý thị trường: Đào ngách, Ngách con & từ khoá, So sánh đa thị trường, Radar từ khoá, bảng RPM "
        "đều dùng danh sách thị trường **đang bật** ở đây. Khu vực và ngôn ngữ theo danh sách chính thức của Google "
        "(YouTube i18nRegions / i18nLanguages). Tắt một thị trường không xoá dữ liệu cũ của nó."
    )
    cat = mc.catalog(conn)
    k1, k2 = st.columns([3, 2])
    k1.caption(
        f"Danh sách Google: {len(cat['regions'])} khu vực, {len(cat['languages'])} ngôn ngữ, tải lúc {cat['fetched_at'][:10]}."
        if cat else "Chưa tải danh sách của Google: bảng dưới chỉ có các thị trường đang lưu."
    )
    if k2.button("🔄 Tải danh sách khu vực và ngôn ngữ của Google (2 unit)", disabled=not cfg.api_key()):
        try:
            mc.refresh_catalog(conn)
            st.rerun()
        except YouTubeError as e:
            st.error(str(e))

    stored = {m["region"]: m for m in mc.list_markets(conn)}
    region_names = {r["code"]: r["name"] for r in (cat or {}).get("regions", [])}
    lang_names = {lang["code"]: lang["name"] for lang in (cat or {}).get("languages", [])}
    for m in stored.values():  # ngôn ngữ đang dùng mà danh sách chưa tải
        lang_names.setdefault(m["hl"], m["hl"])
    lang_label = {code: f"{name} ({code})" for code, name in lang_names.items()}
    label_to_hl = {v: k for k, v in lang_label.items()}

    codes = list(stored) + sorted((c for c in region_names if c not in stored), key=lambda c: region_names[c])
    table = pd.DataFrame([
        {
            "Bật": bool(stored[c]["enabled"]) if c in stored else False,
            "Mã": c,
            "Khu vực": stored[c]["name"] if c in stored else region_names[c],
            "Ngôn ngữ": lang_label.get(stored[c]["hl"] if c in stored else mc.default_hl(c), mc.default_hl(c)),
            "Nhóm": stored[c]["group_name"] if c in stored else "",
        }
        for c in codes
    ])
    query = st.text_input("Lọc theo tên hoặc mã khu vực", placeholder="vd. Tây Ban Nha, ES")
    if query.strip():
        q = query.strip().lower()
        table = table[table["Mã"].str.lower().str.contains(q) | table["Khu vực"].str.lower().str.contains(q)]
    n_on = sum(1 for m in stored.values() if m["enabled"])
    edited = st.data_editor(
        table,
        hide_index=True,
        width="stretch",
        height=460,
        disabled=["Mã"],
        column_config={
            "Bật": st.column_config.CheckboxColumn(width="small"),
            "Ngôn ngữ": st.column_config.SelectboxColumn(
                options=sorted(lang_label.values()), required=True, help="Ngôn ngữ tìm kiếm (relevanceLanguage) của thị trường"
            ),
            "Nhóm": st.column_config.TextColumn(help="Nhóm dùng ở trang So sánh đa thị trường; để trống = Khác"),
        },
        key=f"market_editor_{query}",
    )
    s1, s2 = st.columns([1, 3])
    if s1.button(f"Lưu thị trường (đang bật {n_on})", type="primary"):
        try:
            mc.save(conn, [
                {"region": r["Mã"], "name": r["Khu vực"], "hl": label_to_hl.get(r["Ngôn ngữ"], r["Ngôn ngữ"]),
                 "group_name": r["Nhóm"], "enabled": bool(r["Bật"])}
                for r in edited.to_dict("records")
            ])
            st.success("Đã lưu. Mọi trang dùng danh sách mới.")
            st.rerun()
        except mc.MarketError as e:
            st.error(str(e))
    if s2.button("Về 17 thị trường mặc định"):
        mc.reset_defaults(conn)
        st.rerun()

    if cat:
        with st.expander("Bật nhanh theo ngôn ngữ"):
            hl = st.selectbox(
                "Ngôn ngữ của Google", sorted(lang_names, key=lambda c: lang_names[c]), index=None,
                format_func=lambda c: lang_label[c], placeholder="Chọn ngôn ngữ",
            )
            if hl:
                lang = mc.relevance_language(hl)
                regions = [c for c in region_names if mc.REGION_LANGUAGE.get(c) == lang]
                st.caption("Khu vực dùng ngôn ngữ này: " + (", ".join(f"{region_names[c]} ({c})" for c in regions) or "chưa có trong bảng gợi ý; bật tay ở bảng trên."))
                if st.button("Bật các khu vực này", disabled=not regions):
                    added = mc.enable_language(conn, hl)
                    st.success(f"Đã bật thêm {len(added)} thị trường.")
                    st.rerun()

with tab_thr:
    st.caption("Mục có trong khóa học giữ nguyên mặc định; các mục còn lại là giả định của app, chỉnh theo kinh nghiệm của bạn.")
    values: dict = {}
    groups = dict.fromkeys(s.group for s in settings.EDITABLE)
    for group in groups:
        with st.expander(group, expanded=False):
            for s in (s for s in settings.EDITABLE if s.group == group):
                current = getattr(cfg, s.key)
                default = settings.DEFAULTS[s.key]
                note = f"Mặc định: {default}" + (f". {s.help}" if s.help else "")
                if s.kind == "int":
                    values[s.key] = st.number_input(s.label, value=int(current), step=1, help=note, key=f"thr_{s.key}")
                elif s.kind == "float":
                    values[s.key] = st.number_input(s.label, value=float(current), step=0.05, format="%.2f", help=note, key=f"thr_{s.key}")
                elif s.kind == "pair":
                    st.markdown(s.label, help=note)
                    p1, p2 = st.columns(2)
                    values[s.key] = (
                        p1.number_input("0 điểm", value=float(current[0]), step=0.5, key=f"thr_{s.key}_0"),
                        p2.number_input("100 điểm", value=float(current[1]), step=0.5, key=f"thr_{s.key}_1"),
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

    c1, c2, c3 = st.columns(3)
    if c1.button("Lưu ngưỡng", type="primary"):
        errors = settings.validate(values)
        if errors:
            for e in errors:
                st.error(e)
        else:
            settings.save(values)
            st.success("Đã lưu. Lần đào mới dùng ngưỡng mới ngay.")
    if c2.button("Khôi phục mặc định"):
        settings.save({})
        for k in list(st.session_state):
            if k.startswith("thr_"):
                del st.session_state[k]
        st.rerun()
    if c3.button("Tính lại tất cả lần đào (không tốn quota)"):
        runs = run_repo.list_all(conn)
        with st.spinner(f"Đang tính lại {len(runs)} lần đào…"):
            for r in runs:
                reanalyze(conn, r["id"], category=r["category"], affiliates=r["affiliates"])
        st.success(f"Đã tính lại {len(runs)} lần đào với ngưỡng và bảng RPM hiện tại.")
