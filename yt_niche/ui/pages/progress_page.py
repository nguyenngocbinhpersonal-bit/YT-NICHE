"""Trang Tiến độ chặng: checklist tổng kết chặng Chọn ngách và nhắc đào lại mỗi tháng."""

import streamlit as st

from yt_niche import config as cfg
from yt_niche.services import tracking
from yt_niche.services.runs import redig
from yt_niche.ui.common import get_conn, handle_youtube_errors, market_label

st.title("🧭 Tiến độ chặng Chọn ngách")
st.caption("Đầu ra cuối chặng: 1 ngách chính, 1 ngách dự phòng, kế hoạch 10 video thử kèm mốc xem lại và tín hiệu dừng.")
conn = get_conn()

items = tracking.stage_checklist(conn)
done = sum(i["ok"] for i in items)
st.progress(done / len(items), text=f"**{done}/{len(items)}** mục đã đạt")
for i in items:
    st.markdown(f"{'✅' if i['ok'] else '⬜'} **{i['label']}**  \n<small>{i['detail']}</small>", unsafe_allow_html=True)

st.divider()
st.subheader(f"🔔 Đào lại mỗi tháng ({cfg.REDIG_DAYS} ngày)")
st.caption(
    "Đào lại ngách chính (và dự phòng) với số mới để phát hiện sớm tín hiệu bỏ ngách: Trends 1 năm giảm đều, "
    "kênh nhỏ nổ biến mất. Kết quả hiện ở trang 🎬 10 video thử (mục tín hiệu nên bỏ ngách)."
)
tracked = tracking.tracked(conn)
due = {t["run"]["id"]: t for t in tracking.due_redigs(conn)}
if not tracked:
    st.info("Chưa có ngách nào để theo dõi. Đưa ngách vào bảng chấm hoặc chốt ngách chính trước.")
for t in tracked:
    r = t["run"]
    c1, c2 = st.columns([4, 1])
    flag = "🔔 đến hạn" if r["id"] in due else "đã cập nhật"
    c1.markdown(
        f"**{r['topic']}** · {t['role']} · {market_label(r['region'])} · đào lần cuối {r['created_at'][:10]} · {flag}"
    )
    if r["id"] in due and c2.button("🔄 Đào lại", key=f"redig_{r['id']}", disabled=not cfg.api_key()):
        with st.spinner(f"Đang đào lại \"{r['topic']}\"…"):
            new_id = handle_youtube_errors(redig, conn, r["id"])
        if new_id:
            st.session_state["current_run"] = new_id
            st.rerun()
