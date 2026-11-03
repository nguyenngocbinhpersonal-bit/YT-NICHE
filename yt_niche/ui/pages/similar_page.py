"""Trang Video & kênh tương tự: dán URL video hoặc URL kênh → danh sách kênh và video liên quan (kiểu TubeBuddy)."""

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.services import similar
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError
from yt_niche.ui.common import fmt_num, get_conn, market_label

SIGNAL_LABEL = {"related": "Up next", "search": "Search video", "search_channel": "Search kênh"}
FMT_SHORT = {"long": "Dài", "shorts": "Shorts", "mid": "3–4 phút"}
EST_UNITS = 200  # lần đầu với một ngách (chưa cache kênh); tra lại trong 7 ngày thường dưới 20 unit

st.title("🔗 Video & kênh tương tự")
st.caption(
    "Dán link một video hoặc một kênh. App gom 3 nguồn: cột Up next của YouTube, search theo tiêu đề / tag / gợi ý "
    "tìm kiếm, và kênh lặp lại ở nhiều nguồn; rồi chấm theo độ giống nội dung và độ đồng xuất hiện."
)

conn = get_conn()

with st.form("similar_form"):
    c1, c2 = st.columns([3, 1])
    url = c1.text_input(
        "URL video hoặc URL kênh",
        placeholder="https://www.youtube.com/watch?v=…  hoặc  https://www.youtube.com/@tênkênh",
    )
    markets = cfg.ENABLED_MARKETS
    region = c2.selectbox(
        "Xem như người ở",
        markets,
        index=markets.index("US") if "US" in markets else 0,
        format_func=market_label,
        help="Lấy Up next và search như người xem ở thị trường này. Với Mỹ, YouTube tự dịch tiêu đề sang tiếng Anh, "
        "nên so được cả kênh khác ngôn ngữ.",
    )
    a1, a2 = st.columns(2)
    with_source_market = a1.checkbox(
        "Thêm thị trường của kênh gốc", value=True, help="Lấy thêm Up next ở nước của kênh gốc (nếu kênh có khai báo)"
    )
    fresh = a2.checkbox("Bỏ qua cache (lấy số mới)", value=False)
    submitted = st.form_submit_button("Tìm", type="primary")

if submitted:
    if not url.strip():
        st.error("Dán URL video hoặc URL kênh trước đã.")
    elif not cfg.api_key():
        st.error("Chưa có YouTube API key. Vào trang Cài đặt để nhập.")
    else:
        with st.spinner(f"Đang tìm… (khoảng 10–30 giây, tối đa khoảng {EST_UNITS} unit quota nếu chưa có cache)"):
            try:
                st.session_state["similar_run"] = similar.find_similar(
                    conn, url.strip(), region, with_source_market=with_source_market, fresh=fresh
                )
            except similar.SimilarError as e:
                st.error(str(e))
            except QuotaExceeded:
                st.error("Hết quota YouTube hôm nay. Quota reset lúc 0h giờ Thái Bình Dương (14h–15h giờ VN).")
            except YouTubeError as e:
                st.error(f"Lỗi YouTube API: {e}")

runs = similar.list_runs(conn)
if not runs:
    st.stop()

ids = [r["id"] for r in runs]
current = st.session_state.get("similar_run")
labels = {
    r["id"]: f"{r['label']} · {'video' if r['kind'] == 'video' else 'kênh'} · {market_label(r['region'])} · "
    f"{r['created_at'][:10]} (#{r['id']})"
    for r in runs
}
run_id = st.selectbox(
    "Lần tra", ids, index=ids.index(current) if current in ids else 0, format_func=labels.get, key="similar_pick"
)
run = similar.get_run(conn, run_id)
if not run:
    st.stop()
res = run["result"]
src = res["source"]

st.divider()
if src.get("video_id"):
    st.subheader(f"🎬 {src['video_title']}")
    st.caption(
        f"Kênh **{src['channel']}** {src.get('handle') or ''} · {fmt_num(src['subs'])} sub · "
        f"video {fmt_num(src['video_views'])} view · [mở video](https://www.youtube.com/watch?v={src['video_id']})"
    )
else:
    st.subheader(f"📺 {src['channel']}")
    st.caption(
        f"{src.get('handle') or ''} · {fmt_num(src['subs'])} sub · "
        f"[mở kênh](https://www.youtube.com/channel/{src['channel_id']})"
    )

stats = res["stats"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Kênh tương tự", len(res["channels"]))
m2.metric("Video tương tự", len(res["videos"]))
m3.metric("Quota đã dùng", f"{stats['units']} unit")
m4.metric("Thời gian", f"{stats['elapsed_s']:.0f} giây")
if stats.get("errors"):
    st.warning(
        f"{len(stats['errors'])} lần gọi YouTube (Up next / search) bị lỗi nên kết quả có thể thiếu: "
        + "; ".join(stats["errors"][:3])
    )

with st.expander("Cách app tìm ra danh sách này"):
    st.markdown(
        f"- Thị trường xét Up next: {', '.join(market_label(g) for g in res['locales'])}\n"
        f"- Video xuất phát: " + "; ".join(s["title"] for s in res["seeds"]) + "\n"
        f"- Truy vấn search: " + "; ".join(f"`{q}`" for q in res["queries"]) + "\n"
        f"- Ứng viên gom được: {stats['candidates']['channels']} kênh, {stats['candidates']['videos']} video; "
        f"sau khi lọc lạc đề còn {len(res['channels'])} kênh, {len(res['videos'])} video\n"
        "- **Điểm kênh** = 60% độ giống nội dung + 40% độ đồng xuất hiện. **Điểm video** = 65% độ giống nội dung + "
        "35% số lần xuất hiện. Độ giống nội dung tính so với ứng viên giống nhất trong lần tra này (100% = giống nhất).\n"
        "- Up next lấy ở phiên ẩn danh nên có nhiễu; app loại ứng viên có nội dung giống dưới "
        f"{cfg.SIMILAR_MIN_CONTENT:.0%} và kênh nhạc tự tạo (“- Topic”)."
    )


def _sources(signals: dict) -> str:
    return " · ".join(f"{SIGNAL_LABEL.get(k, k)} ×{v:g}" for k, v in sorted(signals.items(), key=lambda kv: -kv[1]))


f1, f2, f3 = st.columns(3)
min_subs = f1.number_input("Sub tối thiểu", min_value=0, value=0, step=1000)
max_subs = f2.number_input("Sub tối đa (0 = không giới hạn)", min_value=0, value=0, step=10000)
min_views = f3.number_input("Lượt xem video tối thiểu", min_value=0, value=0, step=1000)


def _subs_ok(subs) -> bool:
    if subs is None:
        return min_subs == 0
    return subs >= min_subs and (not max_subs or subs <= max_subs)


tab_c, tab_v = st.tabs([f"📺 Kênh tương tự ({len(res['channels'])})", f"🎬 Video tương tự ({len(res['videos'])})"])

with tab_c:
    rows = [
        {
            "Điểm": c["score"],
            "Kênh": c["channel"],
            "Handle": c.get("handle") or "",
            "Sub": c["subs"],
            "Giống nội dung": round(100 * c["content"]),
            "Đồng xuất hiện": round(100 * c["cooccur"]),
            "Nguồn": _sources(c["signals"]),
            "Video gần đây": " | ".join(c.get("sample", [])),
            "Link": f"https://www.youtube.com/channel/{c['channel_id']}",
        }
        for c in res["channels"]
        if _subs_ok(c["subs"])
    ]
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={
                "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Sub": st.column_config.NumberColumn(format="localized"),
                "Giống nội dung": st.column_config.NumberColumn(format="%d%%"),
                "Đồng xuất hiện": st.column_config.NumberColumn(format="%d%%"),
                "Link": st.column_config.LinkColumn(display_text="Mở kênh"),
            },
        )
        st.download_button(
            "⬇️ Tải CSV kênh",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"kenh_tuong_tu_{run_id}.csv",
            mime="text/csv",
        )
    else:
        st.info("Không có kênh nào khớp bộ lọc.")

with tab_v:
    fmts = st.multiselect(
        "Định dạng", list(FMT_SHORT), default=list(FMT_SHORT), format_func=FMT_SHORT.get, key="similar_fmts"
    )
    rows = [
        {
            "Điểm": v["score"],
            "Tiêu đề": v["title"],
            "Kênh": v["channel"],
            "Lượt xem": v["views"],
            "Outlier": v.get("outlier"),
            "Mức": v.get("level") or "",
            "Định dạng": FMT_SHORT.get(v["fmt"], v["fmt"]),
            "Ngày đăng": (v.get("published_at") or "")[:10],
            "Sub kênh": v.get("subs"),
            "Nguồn": _sources(v["signals"]),
            "Link": f"https://www.youtube.com/watch?v={v['video_id']}",
        }
        for v in res["videos"]
        if v["fmt"] in fmts and (v["views"] or 0) >= min_views and _subs_ok(v.get("subs"))
    ]
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            column_config={
                "Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                "Lượt xem": st.column_config.NumberColumn(format="localized"),
                "Outlier": st.column_config.NumberColumn(
                    format="%.1f×", help="Lượt xem ÷ trung vị lượt xem của chính kênh đó (cùng định dạng)"
                ),
                "Sub kênh": st.column_config.NumberColumn(format="localized"),
                "Link": st.column_config.LinkColumn(display_text="Xem"),
            },
        )
        st.download_button(
            "⬇️ Tải CSV video",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"video_tuong_tu_{run_id}.csv",
            mime="text/csv",
        )
    else:
        st.info("Không có video nào khớp bộ lọc.")

st.divider()
if st.button("🗑️ Xoá lần tra này"):
    similar.delete_run(conn, run_id)
    st.session_state.pop("similar_run", None)
    st.rerun()
