"""Trang Bảng chấm: chốt trọng số → chấm 10 tiêu chí → ngách chính + dự phòng (bài 2.5)."""

import pandas as pd
import streamlit as st

from yt_niche.core import scorecard as sc
from yt_niche.db import runs as run_repo
from yt_niche.services import scorecards as svc
from yt_niche.services.report import FMT_LABEL, build_scorecard_review_prompt
from yt_niche.ui.common import get_conn, market_label, prompt_box

st.title("⚖️ Bảng chấm chốt ngách")
st.caption("Chốt trọng số trước, chấm sau. Ô 1 điểm là cờ đỏ, dù tổng cao đến đâu.")
conn = get_conn()


def _run_label(r: dict) -> str:
    return (
        f"{r['topic']} · {market_label(r['region'])} · {FMT_LABEL[r['fmt']]} · {r['window_days']} ngày · "
        f"{r['created_at'][:10]} · {r['score']:.0f} điểm (#{r['id']})"
    )


# ---------------------------------------------------------------------------
# Tạo / chọn bảng chấm
# ---------------------------------------------------------------------------

boards = svc.list_all(conn)
with st.expander("➕ Tạo bảng chấm mới", expanded=not boards):
    candidates = [r for r in run_repo.list_all(conn) if r["result"]["can_shortlist"]]
    if len(candidates) < svc.MIN_ITEMS:
        st.info("Cần ít nhất 2 lần đào có độ tin cậy vừa hoặc cao. Vào trang Đào ngách để đào thêm.")
    else:
        by_id = {r["id"]: r for r in candidates}
        default = [r["id"] for r in candidates if r["shortlisted"]][: svc.MAX_ITEMS]
        with st.form("create_board"):
            name = st.text_input("Tên bảng chấm", placeholder="vd. Chọn ngách tháng 10")
            picked = st.multiselect(
                f"Chọn {svc.MIN_ITEMS}–{svc.MAX_ITEMS} ngách (mặc định là các ngách đã bật \"Đưa vào bảng chấm\")",
                list(by_id),
                default=default,
                format_func=lambda i: _run_label(by_id[i]),
                max_selections=svc.MAX_ITEMS,
            )
            if st.form_submit_button("Tạo bảng chấm", type="primary"):
                try:
                    st.session_state["board"] = svc.create(conn, name, picked)
                    st.rerun()
                except svc.ScorecardError as e:
                    st.error(str(e))

if not boards:
    st.stop()

board_ids = [b["id"] for b in boards]
labels = {
    b["id"]: f"{b['name']} · v{b['version']} · {b['n_items']} ngách · {'🔒 đã khoá' if b['locked_at'] else '✏️ chưa khoá'}"
    for b in boards
}
current = st.session_state.get("board")
board_id = st.selectbox(
    "Bảng chấm",
    board_ids,
    index=board_ids.index(current) if current in board_ids else 0,
    format_func=labels.get,
)
st.session_state["board"] = board_id
data = svc.load(conn, board_id)
names = {r["id"]: r["topic"] for r in data["runs"]}

for w in data["warnings"]:
    st.warning(f"⚠️ {w}. Bài 2.4: chỉ so các ngách đào cùng thiết lập, cùng ngày.")
if data["missing_runs"]:
    st.warning(f"{data['missing_runs']} lần đào trong bảng đã bị xoá nên không hiện.")
if data["parent_id"]:
    st.caption(f"Phiên bản {data['version']}, tạo từ bảng #{data['parent_id']} (bản cũ vẫn giữ để đối chiếu).")

# ---------------------------------------------------------------------------
# Bước 1: trọng số
# ---------------------------------------------------------------------------

st.subheader("1. Trọng số")
weights = data["weights"]
if not data["locked_at"]:
    st.caption(
        "Trọng số là gợi ý; ví dụ kênh sống bằng affiliate có thể đẩy \"Tiền ngoài quảng cáo\" lên 20 và hạ \"Giá quảng cáo\" "
        "xuống 0. Tổng phải bằng 100. Sau khi khoá thì không sửa được nữa."
    )
    cols = st.columns(2)
    new_weights = {
        c.key: cols[i % 2].number_input(
            c.label, min_value=0, max_value=100, value=int(weights[c.key]), step=5, help=c.guide, key=f"w_{board_id}_{c.key}"
        )
        for i, c in enumerate(sc.CRITERIA)
    }
    total = sum(new_weights.values())
    (st.success if total == 100 else st.error)(f"Tổng: {total}/100")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Lưu trọng số"):
        try:
            svc.set_weights(conn, board_id, new_weights)
            st.rerun()
        except svc.ScorecardError as e:
            st.error(str(e))
    if b2.button("🔒 Khoá trọng số", type="primary", disabled=total != 100):
        try:
            svc.set_weights(conn, board_id, new_weights)
            svc.lock(conn, board_id)
            st.rerun()
        except svc.ScorecardError as e:
            st.error(str(e))
    st.stop()

st.dataframe(
    pd.DataFrame([{"Tiêu chí": c.label, "Trọng số": weights[c.key]} for c in sc.CRITERIA]).set_index("Tiêu chí").T,
    width="stretch",
)
c1, c2 = st.columns([3, 1])
c1.caption(f"🔒 Đã khoá lúc {data['locked_at'][:16].replace('T', ' ')} (UTC).")
if c2.button("Tạo phiên bản mới để đổi trọng số"):
    st.session_state["board"] = svc.new_version(conn, board_id)
    st.rerun()

# ---------------------------------------------------------------------------
# Bước 2: chấm điểm
# ---------------------------------------------------------------------------

st.subheader("2. Chấm điểm 1–5")
h1, h2 = st.columns([3, 1])
h1.caption("App điền sẵn ô tính được từ số liệu đào; bạn xác nhận hoặc sửa, và chấm tay các ô còn trống.")
if h2.button("Cập nhật gợi ý từ số liệu mới"):
    svc.refresh_suggestions(conn, board_id)
    st.rerun()

tabs = st.tabs([names[r["id"]] for r in data["runs"]])
for tab, run in zip(tabs, data["runs"]):
    rid = run["id"]
    cells = data["cells"][rid]
    with tab, st.form(f"score_{board_id}_{rid}"):
        st.caption(_run_label(run))
        values = {}
        for c in sc.CRITERIA:
            cell = cells[c.key]
            if weights[c.key] == 0:
                continue
            left, mid, right = st.columns([2, 2, 3])
            left.markdown(f"**{c.label}** · {weights[c.key]}")
            left.caption(c.guide)
            score = mid.radio(
                c.label,
                [1, 2, 3, 4, 5],
                index=cell["score"] - 1 if cell["score"] else None,
                horizontal=True,
                label_visibility="collapsed",
                key=f"s_{board_id}_{rid}_{c.key}",
            )
            hint = f"Gợi ý: {cell['auto_score']} — {cell['rationale']}" if cell["auto_score"] else cell["rationale"]
            mid.caption(hint)
            note = right.text_input(
                "Căn cứ", value=cell["note"], placeholder="vì sao chấm điểm này", key=f"n_{board_id}_{rid}_{c.key}"
            )
            values[c.key] = (score, note)
        if st.form_submit_button(f"Lưu điểm: {names[rid]}", type="primary"):
            svc.set_scores(conn, board_id, rid, values)
            st.rerun()

# ---------------------------------------------------------------------------
# Bước 3: kết quả
# ---------------------------------------------------------------------------

st.subheader("3. Kết quả")
decision = data["decision"]
active = [c for c in sc.CRITERIA if weights[c.key] > 0]
table = pd.DataFrame(
    [
        {
            "Ngách": r["name"],
            **{c.label: ("🚩 1" if r["scores"].get(c.key) == 1 else r["scores"].get(c.key) or "—") for c in active},
            "Điểm chốt": r["final"],
            "Cờ đỏ": len(r["red_flags"]) or "",
        }
        for r in decision["ranking"]
    ]
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={"Điểm chốt": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")},
)

if not decision["complete"]:
    st.info("Chấm đủ các ô (có trọng số > 0) của mọi ngách để xem ngách chính và dự phòng.")
    st.stop()

flagged = [r for r in decision["ranking"] if r["red_flags"]]
for r in flagged:
    crits = ", ".join(sc.CRITERIA_BY_KEY[k].label for k in r["red_flags"])
    st.error(f"🚩 {r['name']} ({r['final']:g} điểm) có ô 1 điểm ({crits}): không được làm ngách chính.")

main, backup = decision["main"], decision["backup"]
if not main:
    st.error("Không ngách nào đủ điều kiện làm ngách chính (mọi ngách đều có ô dưới 2). Hãy đào ngách khác hoặc ngách con.")
    st.stop()

m, b = st.columns(2)
with m.container(border=True):
    st.markdown(f"### 🎯 Ngách chính: {main['name']}")
    st.metric("Điểm chốt", f"{main['final']:g}/100")
    st.caption(f"Lý do: {decision['main_reason']}")
with b.container(border=True):
    if backup:
        st.markdown(f"### 🛟 Dự phòng: {backup['name']}")
        st.metric("Điểm chốt", f"{backup['final']:g}/100")
        st.caption(f"Lý do: {decision['backup_reason']}")
    else:
        st.markdown("### 🛟 Dự phòng: chưa có")
        st.caption("Không còn ngách nào đủ điều kiện. Hãy đào thêm một ngách gần ngách chính.")

with st.form(f"promise_{board_id}"):
    promise = st.text_input(
        "Lời hứa kênh (một câu, đặt ở đầu mô tả kênh)",
        value=data["promise"],
        placeholder="vd. Hiểu vì sao mình tiêu tiền như vậy, và đổi một thói quen mỗi tuần.",
    )
    close = st.checkbox(
        "Ngách dự phòng gần ngách chính về khán giả và phong cách hình ảnh",
        value=bool(data["backup_close"]),
        help="Để khi chuyển không mất khán giả và không phải làm lại nhận diện kênh.",
    )
    if st.form_submit_button("Lưu"):
        svc.set_promise(conn, board_id, promise, close)
        st.rerun()

if not data["promise"]:
    st.warning("Chưa có lời hứa kênh.")
if backup and not data["backup_close"]:
    st.warning("Dự phòng nên gần ngách chính về khán giả. Nếu không gần, cân nhắc chọn ngách khác làm dự phòng.")
st.caption("Không chạy ngách chính và dự phòng song song trên một kênh; dự phòng chỉ để chuyển khi cần.")

with st.expander("📋 Prompt: phản biện bảng chấm (Claude / ChatGPT)"):
    prompt_box(build_scorecard_review_prompt(data))
st.info("Tiếp theo: vào trang 🎬 10 video thử để lập kế hoạch cho ngách chính.")

st.divider()
if st.button("🗑️ Xoá bảng chấm này"):
    st.session_state["confirm_delete_board"] = board_id
if st.session_state.get("confirm_delete_board") == board_id:
    st.warning("Xoá vĩnh viễn bảng chấm này? Các lần đào vẫn giữ nguyên.")
    d1, d2, _ = st.columns([1, 1, 4])
    if d1.button("Xoá", type="primary"):
        svc.delete(conn, board_id)
        st.session_state.pop("confirm_delete_board")
        st.session_state.pop("board", None)
        st.rerun()
    if d2.button("Huỷ"):
        st.session_state.pop("confirm_delete_board")
        st.rerun()
