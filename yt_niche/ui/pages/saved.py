"""Trang Ngách đã lưu: danh sách lần đào, đào lại với số mới, so số cũ / mới."""

import pandas as pd
import streamlit as st

from yt_niche.db import runs as run_repo
from yt_niche.services.report import FMT_LABEL
from yt_niche.services.runs import compare, redig
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError
from yt_niche.ui.common import LABEL_ICON, VERDICT_ICON, get_conn, market_label, render_run

st.title("🗂️ Ngách đã lưu")
conn = get_conn()


def _cell(x, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    if isinstance(x, (int, float)):
        return f"{x:+,.1f}" if signed else f"{x:,.1f}"
    return str(x)



all_runs = run_repo.list_all(conn)
if not all_runs:
    st.info("Chưa có lần đào nào. Vào trang Đào ngách để bắt đầu.")
    st.stop()

query = st.text_input("Lọc theo chủ đề", placeholder="gõ một phần tên chủ đề")
only_short = st.checkbox("Chỉ hiện ngách đã đưa vào bảng chấm")
runs = [
    r
    for r in all_runs
    if query.lower() in r["topic"].lower() and (r["shortlisted"] or not only_short)
]

table = pd.DataFrame(
    [
        {
            "#": r["id"],
            "Chủ đề": r["topic"],
            "Tầng": r.get("layer") or 1,
            "Thị trường": market_label(r["region"]),
            "Định dạng": FMT_LABEL[r["fmt"]],
            "Khoảng": f"{r['window_days']} ngày",
            "Ngày đào": r["created_at"][:10],
            "Điểm": round(r["score"]),
            "Nhãn": f"{LABEL_ICON.get(r['label'], '')} {r['label']}",
            "Tin cậy": r["confidence"],
            "Kết luận": f"{VERDICT_ICON.get(r['verdict'], '')} {r['verdict']}",
            "Bảng chấm": "⭐" if r["shortlisted"] else "",
        }
        for r in runs
    ]
)
event = st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    on_select="rerun",
    selection_mode="single-row",
    column_config={"Điểm": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")},
)

rows = event.selection.rows
if not rows:
    st.caption("Chọn một dòng để xem chi tiết, đào lại hoặc so sánh.")
    st.stop()

selected = runs[rows[0]]
st.divider()

c1, c2, _ = st.columns([1, 1, 3])
if c1.button("🔄 Đào lại (số mới)", type="primary", help="Cùng thiết lập, bỏ qua cache; tốn khoảng 200–300 unit"):
    with st.spinner("Đang đào lại…"):
        try:
            new_id = redig(conn, selected["id"])
            st.success(f"Đã đào lại: lần đào #{new_id}. Bảng so sánh ở dưới.")
            st.session_state["compare_pair"] = (selected["id"], new_id)
            st.rerun()
        except QuotaExceeded:
            st.error("Hết quota YouTube hôm nay.")
        except YouTubeError as e:
            st.error(f"Lỗi YouTube API: {e}")

if c2.button("🗑️ Xoá lần đào này"):
    st.session_state["confirm_delete"] = selected["id"]
if st.session_state.get("confirm_delete") == selected["id"]:
    st.warning(f"Xoá vĩnh viễn lần đào #{selected['id']} ({selected['topic']})?")
    d1, d2, _ = st.columns([1, 1, 4])
    if d1.button("Xoá", type="primary"):
        run_repo.delete(conn, selected["id"])
        st.session_state.pop("confirm_delete")
        st.rerun()
    if d2.button("Huỷ"):
        st.session_state.pop("confirm_delete")
        st.rerun()

# ---- So số cũ / mới cùng thiết lập ----
history = run_repo.same_setup(conn, selected)
if len(history) >= 2:
    with st.container(border=True):
        st.markdown(f"**So sánh các lần đào cùng thiết lập** ({len(history)} lần)")
        labels = {h["id"]: f"#{h['id']} · {h['created_at'][:16].replace('T', ' ')} · {h['score']:.0f} điểm" for h in history}
        ids = list(labels)
        default_old, default_new = st.session_state.get("compare_pair", (ids[1], ids[0]))
        if default_old not in ids or default_new not in ids:
            default_old, default_new = ids[1], ids[0]
        s1, s2 = st.columns(2)
        old_id = s1.selectbox("Lần cũ", ids, index=ids.index(default_old), format_func=labels.get)
        new_id = s2.selectbox("Lần mới", ids, index=ids.index(default_new), format_func=labels.get)
        by_id = {h["id"]: h for h in history}
        df = pd.DataFrame(compare(by_id[old_id], by_id[new_id]))
        df["Cũ"] = df["Cũ"].map(_cell)
        df["Mới"] = df["Mới"].map(_cell)
        df["Chênh"] = df["Chênh"].map(lambda x: _cell(x, signed=True))
        st.dataframe(df, hide_index=True, width="stretch")
        st.caption("Chênh dưới 5 điểm coi như hoà. Kênh nhỏ nổ biến mất khi đào lại là tín hiệu nên cân nhắc bỏ ngách.")

st.divider()
render_run(run_repo.get(conn, selected["id"], with_raw=True))
