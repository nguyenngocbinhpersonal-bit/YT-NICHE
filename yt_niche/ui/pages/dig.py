"""Trang Đào ngách (bài 2.1 + 2.2)."""

import streamlit as st

from yt_niche import config as cfg
from yt_niche.db import runs as run_repo
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError
from yt_niche.ui.common import EST_UNITS_PER_DIG, NO_CATEGORY, get_conn, market_label, render_run

st.title("⛏️ Đào ngách")
st.caption("Đo Cầu × Cung × Tiền bằng số. Một tầng bằng 0 thì cả ngách bằng 0.")

conn = get_conn()

with st.form("dig_form"):
    c1, c2 = st.columns([2, 1])
    topic = c1.text_input("Chủ đề (2–4 từ)", placeholder="kể chuyện lịch sử")
    markets = cfg.ENABLED_MARKETS
    region = c2.selectbox(
        "Thị trường", markets, index=markets.index("VN") if "VN" in markets else 0, format_func=market_label,
        help="Danh sách thị trường sửa ở Cài đặt → Thị trường",
    )

    c3, c4, c5 = st.columns(3)
    window_days = c3.selectbox("Khoảng thời gian", [7, 30, 90, 365], index=1, format_func=lambda d: f"{d} ngày")
    fmt = c4.radio(
        "Định dạng",
        ["any", "long", "shorts"],
        format_func={"any": "Mọi độ dài", "long": "Video dài", "shorts": "Shorts"}.get,
        horizontal=True,
    )
    category = c5.selectbox("Nhóm chủ đề (RPM)", [NO_CATEGORY] + rpm_source.categories())

    with st.expander("Tuỳ chọn nâng cao"):
        a1, a2, a3, a4 = st.columns(4)
        order = a1.selectbox("Sắp kết quả search", ["relevance", "viewCount"], format_func={"relevance": "Liên quan", "viewCount": "Lượt xem"}.get)
        pages = a2.selectbox("Số trang search", [1, 2], index=1, help="Mỗi trang 50 video, tốn 100 unit")
        with_trends = a3.checkbox("Kèm Google Trends", value=True)
        trends_source = a4.selectbox("Nguồn Trends", ["youtube", "web"], format_func={"youtube": "YouTube", "web": "Google Web"}.get)
        fresh = st.checkbox("Bỏ qua cache (lấy số mới, tốn quota)", value=False)

    affiliates_text = st.text_area(
        "Sản phẩm affiliate gắn tự nhiên (không bắt buộc, mỗi dòng một sản phẩm)", height=80
    )
    submitted = st.form_submit_button("Đào", type="primary")

if submitted:
    if not topic.strip():
        st.error("Nhập chủ đề trước đã.")
    elif not cfg.api_key():
        st.error("Chưa có YouTube API key. Vào trang Cài đặt để nhập.")
    else:
        params = DigParams(
            topic=topic.strip(),
            region=region,
            language=cfg.MARKETS[region][0],
            window_days=window_days,
            fmt=fmt,
            order=order,
            pages=pages,
            with_trends=with_trends,
            trends_source=trends_source,
        )
        affiliates = [line.strip() for line in affiliates_text.splitlines() if line.strip()]
        with st.spinner(f"Đang đào… (tối đa khoảng {EST_UNITS_PER_DIG} unit quota nếu chưa có cache)"):
            try:
                st.session_state["current_run"] = dig(
                    conn,
                    params,
                    category=None if category == NO_CATEGORY else category,
                    affiliates=affiliates,
                    fresh=fresh,
                )
            except QuotaExceeded:
                st.error("Hết quota YouTube hôm nay. Quota reset lúc 0h giờ Thái Bình Dương (14h–15h giờ VN).")
            except YouTubeError as e:
                st.error(f"Lỗi YouTube API: {e}")

run_id = st.session_state.get("current_run")
if run_id and (run := run_repo.get(conn, run_id, with_raw=True)):
    st.divider()
    render_run(run)
