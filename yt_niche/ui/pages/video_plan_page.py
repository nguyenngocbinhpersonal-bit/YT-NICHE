"""Trang 10 video thử: kế hoạch 3 trụ, số đo sau 7 ngày, mốc video 5 và 10 (bài 2.5)."""

from datetime import date

import pandas as pd
import streamlit as st

from yt_niche.core import test_plan as tp
from yt_niche.db import runs as run_repo
from yt_niche.services import scorecards
from yt_niche.services import test_plans as svc
from yt_niche.services.report import build_test_plan_prompt
from yt_niche.ui.common import get_conn, prompt_box

DECISION_ICON = {tp.KEEP: "✅", tp.ADJUST: "🛠️", tp.SWITCH: "🔀"}

st.title("🎬 10 video thử")
st.caption("5–10 video thử với mốc xem lại ở video 5 và quyết định ở video 10. Số liệu cho xác suất, không cho chắc chắn.")
conn = get_conn()

# ---- Chọn bảng chấm đã có ngách chính ----
boards = []
for b in scorecards.list_all(conn):
    if b["locked_at"]:
        data = scorecards.load(conn, b["id"])
        if data["decision"] and data["decision"]["main"]:
            boards.append(data)
if not boards:
    st.info("Chưa có bảng chấm nào chốt được ngách chính. Vào trang ⚖️ Bảng chấm trước.")
    st.stop()

by_id = {b["id"]: b for b in boards}
ids = list(by_id)
current = st.session_state.get("board")
board_id = st.selectbox(
    "Bảng chấm",
    ids,
    index=ids.index(current) if current in ids else 0,
    format_func=lambda i: f"{by_id[i]['name']} v{by_id[i]['version']} → ngách chính: {by_id[i]['decision']['main']['name']}",
)
st.session_state["board"] = board_id
board = by_id[board_id]
main = board["decision"]["main"]
backup = board["decision"]["backup"]
main_run = run_repo.get(conn, main["id"])

st.markdown(
    f"**Ngách chính:** {main['name']} ({main['final']:g} điểm) · "
    f"**Dự phòng:** {backup['name'] if backup else 'chưa có'}"
)
st.markdown(f"**Lời hứa kênh:** {board['promise'] or '_chưa viết, thêm ở trang Bảng chấm_'}")

plan = svc.get(conn, board_id)

# ---------------------------------------------------------------------------
# Chưa có kế hoạch: tạo mới
# ---------------------------------------------------------------------------

if plan is None:
    st.subheader("Lập kế hoạch")
    per_week = st.number_input("Số video dài mỗi tuần", min_value=1, max_value=7, value=2)
    with st.expander("📋 Prompt: gợi ý 3 trụ, 10 tiêu đề, hook, ảnh bìa (Claude / ChatGPT)"):
        style = st.text_input("Kiểu kênh dự định", placeholder="vd. không lộ mặt, giọng AI, video 8–12 phút")
        prompt_box(
            build_test_plan_prompt(
                board, main_run, per_week=int(per_week), channel_style=style, keywords=svc.keyword_ideas(conn, main_run)
            )
        )
    with st.form("create_plan"):
        st.caption("Mỗi trụ là một mảng nội dung; 10 video xoay vòng qua 3 trụ (4 / 3 / 3), sửa lại được sau.")
        cols = st.columns(3)
        pillars = [cols[i].text_input(f"Trụ {i + 1}", key=f"p{i}") for i in range(3)]
        start = st.date_input("Ngày đăng video đầu", value=date.today(), format="DD/MM/YYYY")
        st.caption("Nên làm sẵn 3–5 video trước khi đăng video đầu.")
        if st.form_submit_button("Tạo kế hoạch", type="primary"):
            try:
                svc.create(conn, board_id, pillars, start, int(per_week))
                st.rerun()
            except svc.PlanError as e:
                st.error(str(e))
    st.stop()

# ---------------------------------------------------------------------------
# Đã có kế hoạch
# ---------------------------------------------------------------------------

if plan["run_id"] != main["id"]:
    st.warning("Ngách chính của bảng chấm đã đổi so với lúc lập kế hoạch này.")

videos = plan["videos"]
for issue in tp.plan_issues(plan["pillars"], videos):
    st.warning(issue)

tab_plan, tab_measure, tab_decide, tab_prompt = st.tabs(["Kế hoạch", "Số đo sau 7 ngày", "Mốc video 5 và 10", "Prompt"])


def _to_date(s):
    return date.fromisoformat(s) if s else None


with tab_plan:
    with st.expander("Đổi tên 3 trụ"):
        with st.form("rename"):
            cols = st.columns(3)
            names = [cols[i].text_input(f"Trụ {i + 1}", value=p) for i, p in enumerate(plan["pillars"])]
            if st.form_submit_button("Lưu tên trụ"):
                try:
                    svc.rename_pillars(conn, board_id, names)
                    st.rerun()
                except svc.PlanError as e:
                    st.error(str(e))

    df = pd.DataFrame(
        [
            {
                "idx": v["idx"],
                "pillar": v["pillar"],
                "title": v["title"],
                "chars": len(v["title"]),
                "hook": v["hook"],
                "thumbnail": v["thumbnail"],
                "planned_date": _to_date(v["planned_date"]),
                "published_at": _to_date(v["published_at"]),
            }
            for v in videos
        ]
    )
    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["idx", "chars"],
        column_config={
            "idx": st.column_config.NumberColumn("#", width="small"),
            "pillar": st.column_config.SelectboxColumn("Trụ", options=plan["pillars"], required=True),
            "title": st.column_config.TextColumn("Tiêu đề (< 70 ký tự, từ khoá chính ở đầu)", width="large"),
            "chars": st.column_config.NumberColumn("Ký tự", width="small"),
            "hook": st.column_config.TextColumn("Hook 15 giây đầu"),
            "thumbnail": st.column_config.TextColumn("Ý tưởng ảnh bìa"),
            "planned_date": st.column_config.DateColumn("Ngày dự kiến", format="DD/MM/YYYY"),
            "published_at": st.column_config.DateColumn("Ngày đã đăng", format="DD/MM/YYYY"),
        },
        key=f"plan_editor_{board_id}",
    )
    if st.button("Lưu kế hoạch", type="primary"):
        svc.save_videos(conn, board_id, edited.drop(columns=["chars"]).to_dict("records"))
        st.rerun()

    ideas = svc.keyword_ideas(conn, main_run)
    with st.expander(f"💡 Chủ đề từ từ khoá dài ({len(ideas)}/10 từ đạt ≥ 55) và video vượt trội"):
        st.caption("Bài 2.5: chọn 10 chủ đề từ danh sách từ khoá dài và các video vượt trội; đặt từ khoá chính ở đầu tiêu đề.")
        if ideas:
            st.dataframe(
                pd.DataFrame([{"Từ khoá dài": k["keyword"], "Điểm": round(k["score"]), "Đã chọn đào": "⭐" if k["picked"] else ""} for k in ideas]),
                hide_index=True,
                width="stretch",
            )
            if st.button("Điền từ khoá vào các tiêu đề còn trống (tiêu đề nháp)"):
                n = svc.fill_titles(conn, board_id, [k["keyword"] for k in ideas])
                st.success(f"Đã điền {n} tiêu đề nháp. Sửa lại cho hấp dẫn, giữ từ khoá ở đầu.")
                st.rerun()
        else:
            st.caption("Chưa có từ khoá dài đạt ≥ 55 cho ngách chính: chấm ở trang 🌿 Ngách con & từ khoá.")
        top = main_run["result"]["outliers"][:10]
        if top:
            st.markdown("**Video vượt trội của ngách chính** (học góc kể, không sao chép)")
            for v in top:
                st.markdown(f"- {v['title']} · {v['outlier']:.1f}×")

with tab_measure:
    st.caption(
        "Nhập số từ YouTube Studio sau 7 ngày kể từ khi đăng mỗi video. CTR và giữ chân tính bằng %. "
        "Thời lượng xem trung bình tính bằng giây."
    )
    mdf = pd.DataFrame(
        [
            {
                "idx": v["idx"],
                "title": v["title"],
                "impressions": v["impressions"],
                "ctr": v["ctr"],
                "views": v["views"],
                "avd_seconds": v["avd_seconds"],
                "retention": v["retention"],
                "new_subs": v["new_subs"],
                "asks_more": v["asks_more"],
                "note": v["note"],
            }
            for v in videos
        ]
    )
    medited = st.data_editor(
        mdf,
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        disabled=["idx", "title"],
        column_config={
            "idx": st.column_config.NumberColumn("#", width="small"),
            "title": st.column_config.TextColumn("Tiêu đề"),
            "impressions": st.column_config.NumberColumn("Hiển thị", min_value=0, step=1),
            "ctr": st.column_config.NumberColumn("CTR %", min_value=0.0, max_value=100.0, format="%.1f"),
            "views": st.column_config.NumberColumn("Lượt xem", min_value=0, step=1),
            "avd_seconds": st.column_config.NumberColumn("TG xem TB (giây)", min_value=0, step=1),
            "retention": st.column_config.NumberColumn("Giữ chân %", min_value=0.0, max_value=100.0, format="%.0f"),
            "new_subs": st.column_config.NumberColumn("Sub mới", step=1),
            "asks_more": st.column_config.CheckboxColumn("Bình luận hỏi thêm"),
            "note": st.column_config.TextColumn("Bình luận đáng chú ý / ghi chú"),
        },
        key=f"measure_editor_{board_id}",
    )
    if st.button("Lưu số đo", type="primary"):
        svc.save_videos(conn, board_id, medited.drop(columns=["title"]).to_dict("records"))
        st.rerun()

with tab_decide:
    review = tp.review_at_5(videos)
    with st.container(border=True):
        st.markdown("#### Mốc video 5: xem lại")
        st.caption(review["message"])
        for a in review["actions"]:
            st.markdown(f"- {a}")

    with st.form("flags"):
        fixes = st.checkbox(
            "Đã chỉnh tiêu đề / ảnh bìa / hook sau mốc video 5", value=bool(plan["fixes_applied"])
        )
        out_of_ideas = st.checkbox("Đã cạn ý trước video 10", value=bool(plan["out_of_ideas"]))
        st.markdown("**Tín hiệu nên bỏ ngách (kể cả khi có lượt xem)**")
        history = run_repo.same_setup(conn, main_run) if main_run else []
        for hint in tp.auto_drop_hints(history):
            st.caption(f"⚠️ Từ số liệu đào lại: {hint}")
        drop = [k for k, label in tp.DROP_SIGNALS.items() if st.checkbox(label, value=k in plan["drop_flags"], key=f"drop_{k}")]
        if st.form_submit_button("Lưu"):
            svc.set_flags(conn, board_id, fixes_applied=fixes, out_of_ideas=out_of_ideas, drop_flags=drop)
            st.rerun()

    if plan["drop_flags"]:
        st.error(
            f"Đã tick {len(plan['drop_flags'])} tín hiệu nên bỏ ngách. Cân nhắc chuyển sang ngách dự phòng"
            + (f" ({backup['name']})." if backup else ".")
        )

    result = tp.decide_at_10(videos, fixes_applied=bool(plan["fixes_applied"]), out_of_ideas=bool(plan["out_of_ideas"]))
    with st.container(border=True):
        st.markdown("#### Mốc video 10: quyết định")
        if result["decision"]:
            st.markdown(f"## {DECISION_ICON[result['decision']]} {result['decision']}")
        for r in result["reasons"]:
            st.markdown(f"- {r}")
        for a in result["actions"]:
            st.markdown(f"- 👉 {a}")
        if result["decision"] == tp.SWITCH and backup:
            st.info(f"Ngách dự phòng: **{backup['name']}**. Không chạy song song hai ngách trên một kênh.")

    diag = result["diag"]
    if diag["n"]:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Video đã đo", diag["n"])
        c2.metric("Trung vị lượt xem", f"{diag['median_views']:,.0f}")
        c3.metric("Trung vị CTR", "—" if diag["median_ctr"] is None else f"{diag['median_ctr']:.1f}%")
        c4.metric("Trung vị giữ chân", "—" if diag["median_retention"] is None else f"{diag['median_retention']:.0f}%")
        chart = pd.DataFrame([{"Video": v["idx"], "Lượt xem": v["views"]} for v in tp.measured(videos)]).set_index("Video")
        st.bar_chart(chart, height=220)

with tab_prompt:
    style = st.text_input("Kiểu kênh dự định", placeholder="vd. không lộ mặt, giọng AI, video 8–12 phút", key="style2")
    prompt_box(build_test_plan_prompt(board, main_run, per_week=plan["per_week"], channel_style=style))

st.divider()
if st.button("🗑️ Xoá kế hoạch này"):
    st.session_state["confirm_delete_plan"] = board_id
if st.session_state.get("confirm_delete_plan") == board_id:
    st.warning("Xoá kế hoạch và toàn bộ số đo đã nhập?")
    d1, d2, _ = st.columns([1, 1, 4])
    if d1.button("Xoá", type="primary"):
        svc.delete(conn, board_id)
        st.session_state.pop("confirm_delete_plan")
        st.rerun()
    if d2.button("Huỷ"):
        st.session_state.pop("confirm_delete_plan")
        st.rerun()
