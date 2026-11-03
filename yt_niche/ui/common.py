"""Thành phần dùng chung giữa các trang: kết nối DB, thanh quota, hiển thị một lần đào."""

from __future__ import annotations

import hashlib
import threading
import time

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.db import quota
from yt_niche.db import runs as run_repo
from yt_niche.db.connection import connect
from yt_niche.core import reading
from yt_niche.core.niche_score import SIZE_BIG, SIZE_HIDDEN, SIZE_MID, SIZE_SMALL, all_channels
from yt_niche.services import report
from yt_niche.services.runs import reanalyze, refetch_trends
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources import trends as trends_source

NO_CATEGORY = "(chưa chọn)"
TIER_ICON = {"Mạnh": "🟢", "Vừa": "🟡", "Yếu": "🔴"}
VERDICT_ICON = {"THỬ": "✅", "THỬ SAU KHI SỬA": "🛠️", "BỎ": "⛔"}
LABEL_ICON = {"Ngách xanh": "🟢", "Ngách tiềm năng": "🟡", "Ngách đỏ": "🔴"}
TIER_NAMES = {"demand": "Cầu", "supply": "Cung", "money": "Tiền"}
# Ước lượng unit cho một lần đào 2 trang search khi chưa cache kênh (đo ở mốc 0).
EST_UNITS_PER_DIG = 320


@st.cache_resource
def get_conn():
    conn = connect()
    from yt_niche.services.market_catalog import apply as apply_markets

    apply_markets(conn)  # danh sách thị trường duy nhất (Cài đặt → Thị trường)
    return conn


@st.cache_resource
def start_radar_thread() -> threading.Thread:
    """Khi app mở, chạy Radar từ khoá ở nền (mỗi phút xem đã đến lượt chưa). Cùng khoá DB với Task Scheduler."""
    from yt_niche.services import radar

    def loop():
        conn = connect()
        while True:
            try:
                radar.tick(conn)
            except Exception:  # lỗi một lượt không được dừng vòng lặp
                pass
            time.sleep(60)

    t = threading.Thread(target=loop, name="radar", daemon=True)
    t.start()
    return t


def quota_sidebar() -> int:
    used = quota.used_on(get_conn())
    left = cfg.YOUTUBE_DAILY_QUOTA - used
    with st.sidebar:
        st.caption("Quota YouTube hôm nay (reset 0h giờ PT)")
        st.progress(min(1.0, used / cfg.YOUTUBE_DAILY_QUOTA), text=f"{used:,} / {cfg.YOUTUBE_DAILY_QUOTA:,} unit")
        st.caption(f"Còn khoảng {max(0, left) // EST_UNITS_PER_DIG} lần đào mới")
        if not cfg.api_key():
            st.warning("Chưa có YouTube API key, vào trang Cài đặt.")
        try:
            from yt_niche.services.tracking import due_redigs

            due = due_redigs(get_conn())
        except Exception:  # nhắc nhở không được làm hỏng thanh bên
            due = []
        if due:
            st.info(f"🔔 {len(due)} ngách đến hạn đào lại hằng tháng: xem trang Tiến độ chặng.")
        try:
            from yt_niche.services.radar import new_good_count

            good = new_good_count(get_conn())
        except Exception:
            good = 0
        if good:
            st.info(f"📡 {good} từ khoá tiềm năng mới trong 24 giờ: xem trang Radar từ khoá.")
    return left


def market_label(code: str) -> str:
    return f"{cfg.MARKETS[code][1]} ({code})" if code in cfg.MARKETS else code


def fmt_num(x) -> str:
    return report.fmt_num(x)


# ---------------------------------------------------------------------------
# Hiển thị một lần đào
# ---------------------------------------------------------------------------


def render_run(run: dict) -> None:
    conn = get_conn()
    r = run["result"]
    p = r["params"]
    key = f"run{run['id']}"

    st.subheader(f"{p['topic']}")
    st.caption(
        f"{market_label(p['region'])} · {report.FMT_LABEL[p['fmt']]} · {p['window_days']} ngày · "
        f"đào lúc {run['created_at'][:16].replace('T', ' ')} (UTC) · lần đào #{run['id']}"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Điểm ngách", f"{r['score']:.0f}/100")
    c2.metric("Nhãn", f"{LABEL_ICON.get(r['labels'][0], '')} {r['labels'][0]}")
    c3.metric("Độ tin cậy", r["confidence"], help=f"{r['n']} video, {r['n_estimated']} số ước tính")
    verdict = r["three_tier"]["verdict"]
    c4.metric("Kết luận", f"{VERDICT_ICON[verdict]} {verdict}")
    advice = reading.confidence_advice(r["confidence"])
    (st.warning if r["confidence"] == "thấp" else st.caption)(advice)
    st.info(r["note"])
    st.caption(f"{LABEL_ICON.get(r['labels'][0], '')} {r['labels'][0]}: {reading.zone_advice(r['score'])}")
    if r["n_estimated"]:
        st.caption(f"{r['n_estimated']} số ước tính (≈). {reading.ESTIMATE_NOTE}")
    if warn := reading.topic_warning(p["topic"], p["language"]):
        st.caption(f"⚠️ {warn}")
    _render_seven_cells(run)

    with st.container(border=True):
        st.markdown("**4 thanh con**")
        bars = [
            ("Nhu cầu (35%)", r["demand"], f"trung vị {fmt_num(r['median_views'])} view"),
            ("Cơ hội (25%)", r["opportunity"], "% video vượt trội đến từ kênh nhỏ"),
            ("Độ mới (20%)", r["freshness"], "video gần đây / view mỗi giờ"),
            ("Cạnh tranh (20%, trừ điểm)", r["competition"], "% video của kênh > 1 triệu sub"),
        ]
        for name, value, hint in bars:
            st.progress(value / 100, text=f"{name}: **{value:.0f}** · {hint}")

    _render_three_tier(run, key)
    _render_money_inputs(run, key)

    raw = run.get("raw") or run_repo.get(get_conn(), run["id"], with_raw=True)["raw"]
    channel_rows = all_channels(raw)
    tab_out, tab_ch, tab_all, tab_fmt, tab_tr = st.tabs(
        [
            f"Video vượt trội ({r['outlier_counts']['total']})",
            f"Kênh nhỏ đang nổ ({len(r['small_boom_channels'])})",
            f"Tất cả kênh ({len(channel_rows)})",
            "Dài / Shorts",
            f"Google Trends ({r['trends']['class'] or 'chưa có'})",
        ]
    )
    with tab_out:
        _render_outliers(r)
        _render_questions(run, key)
    with tab_ch:
        _render_small_channels(run, key)
    with tab_all:
        _render_all_channels(run, channel_rows, key)
    with tab_fmt:
        _render_format_split(r)
        _render_format_redig(run, key)
    with tab_tr:
        _render_trends(run)

    _render_actions(run, key)


def _render_seven_cells(run: dict) -> None:
    sheet = reading.seven_cells(run)
    title = "✅ Phiếu 7 ô: đủ" if sheet["complete"] else f"📝 Phiếu 7 ô: thiếu {', '.join(sheet['missing'])}"
    with st.expander(title, expanded=False):
        for c in sheet["cells"]:
            st.markdown(f"{'✅' if c['ok'] else '⬜'} **{c['label']}**: {c['value']}")
        st.caption("Đủ 7 ô (độ tin cậy vừa hoặc cao, có Trends) mới đưa vào so sánh ở trang So sánh.")


def _render_three_tier(run: dict, key: str) -> None:
    t = run["result"]["three_tier"]
    with st.container(border=True):
        st.markdown("**Ba tầng: Cầu × Cung × Tiền** (một tầng bằng 0 thì cả ngách bằng 0)")
        cols = st.columns(3)
        for col, tier in zip(cols, ("demand", "supply", "money")):
            level = t[tier]["level"]
            col.markdown(f"{TIER_ICON[level]} **{TIER_NAMES[tier]}: {level}**")
            col.caption(t[tier]["reason"])
        for fix in t["fixes"]:
            st.markdown(f"- 🛠️ {fix}")


def _render_money_inputs(run: dict, key: str) -> None:
    r = run["result"]
    with st.expander("Tiền: nhóm chủ đề (RPM) và sản phẩm affiliate", expanded=not run["affiliates"]):
        cats = [NO_CATEGORY] + rpm_source.categories()
        current = run["category"] if run["category"] in cats else NO_CATEGORY
        category = st.selectbox("Nhóm chủ đề (để tra RPM)", cats, index=cats.index(current), key=f"{key}_cat")
        affiliates_text = st.text_area(
            "Sản phẩm affiliate gắn tự nhiên (mỗi dòng một sản phẩm)",
            value="\n".join(run["affiliates"]),
            key=f"{key}_aff",
            placeholder="Sách lịch sử\nBản đồ cổ in lại\nTruyện tranh lịch sử",
        )
        if r["rpm"]:
            income = r["income_per_video"]
            st.caption(
                f"RPM ≈ {r['rpm'][0]:g}–{r['rpm'][1]:g} USD (bậc {r['rpm_tier']}) → thu nhập/video ≈ "
                f"{income[0]:g}–{income[1]:g} USD. Ước tính để so ngách, không phải thu nhập thật."
            )
        st.caption(reading.MONETIZATION)
        st.caption("Muốn xem thu nhập ước tính đổi theo thị trường: trang 🌍 So sánh → Đa thị trường.")
        if st.button("Tính lại (không tốn quota)", key=f"{key}_reanalyze"):
            affiliates = [line.strip() for line in affiliates_text.splitlines() if line.strip()]
            reanalyze(get_conn(), run["id"], category=None if category == NO_CATEGORY else category, affiliates=affiliates)
            st.rerun()


def _render_outliers(r: dict) -> None:
    if not r["outliers"]:
        st.write("Không có video nào vượt ≥ 3× trung vị kênh của nó.")
        return
    df = pd.DataFrame(
        [
            {
                "Mức ×": round(v["outlier"], 1),
                "Nhãn": v["level"],
                "Tiêu đề": v["title"],
                "Lượt xem": v["views"],
                "Trung vị kênh": v["channel_median"],
                "Sub kênh": v["subs"],
                "Định dạng": report.FMT_LABEL.get(v["fmt"], v["fmt"]),
                "Ngày đăng": v["published_at"][:10],
                "Mở": f"https://www.youtube.com/watch?v={v['video_id']}",
            }
            for v in r["outliers"]
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Mở": st.column_config.LinkColumn(display_text="▶"),
            "Lượt xem": st.column_config.NumberColumn(format="localized"),
            "Trung vị kênh": st.column_config.NumberColumn(format="localized"),
            "Sub kênh": st.column_config.NumberColumn(format="localized"),
        },
    )


def _render_questions(run: dict, key: str) -> None:
    from yt_niche.services.comments import MAX_VIDEOS, questions_for_run

    if not run["result"]["outliers"]:
        return
    n = min(MAX_VIDEOS, len(run["result"]["outliers"]))
    state_key = f"{key}_questions"
    if st.button(
        f"❓ Bình luận dạng câu hỏi ở {n} video vượt trội đầu (≈ {n} unit)",
        key=f"{key}_qbtn",
        help="Câu hỏi chưa được trả lời là nhu cầu chưa được đáp ứng và là nguồn ý tưởng video (bài 2.1).",
        disabled=not cfg.api_key(),
    ):
        with st.spinner("Đang đọc bình luận…"):
            got = handle_youtube_errors(questions_for_run, get_conn(), run)
        if got is not None:
            st.session_state[state_key] = got
    got = st.session_state.get(state_key)
    if got:
        st.caption(f"{len(got['questions'])} câu hỏi trong {got['n_comments']} bình luận của {got['n_videos']} video.")
        if got["questions"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Câu hỏi": q["question"][:300], "Thích": q["likes"], "Trả lời": q["replies"], "Video": q["video_title"]}
                        for q in got["questions"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )


def _render_all_channels(run: dict, rows: list[dict], key: str) -> None:
    if not rows:
        st.caption("Không có kênh nào.")
        return
    st.caption(
        "Mọi kênh có video trong kết quả tìm từ khoá của lần đào này (lấy từ dữ liệu đã lưu, không tốn quota). "
        f"Kênh nhỏ < {fmt_num(cfg.SMALL_CHANNEL_MAX_SUBS)} sub, lớn ≥ {fmt_num(cfg.BIG_CHANNEL_MIN_SUBS)} sub. "
        "Số kênh phụ thuộc mẫu tìm được: mỗi trang search 50 video (100 unit), chọn 2 trang ở form Đào để có mẫu lớn hơn."
    )
    sizes = [SIZE_SMALL, SIZE_MID, SIZE_BIG, SIZE_HIDDEN]
    counts = {s: sum(r["size"] == s for r in rows) for s in sizes}
    picked = st.multiselect(
        "Cỡ kênh", sizes, default=sizes, key=f"{key}_allch_size",
        format_func=lambda s: f"{s} ({counts[s]})",
    )
    shown = [r for r in rows if r["size"] in picked]
    df = pd.DataFrame(
        [
            {
                "Kênh": r["title"],
                "Link": f"https://www.youtube.com/channel/{r['channel_id']}",
                "Sub": r["subs"],
                "Cỡ": r["size"],
                "Tuổi (tháng)": r["age_months"],
                "Video của kênh": r["video_count"],
                "Video trong mẫu": r["n_videos"],
                "Tổng view trong mẫu": r["total_views"],
                "Vượt trội": r["n_outliers"],
                "Hệ số cao nhất": r["best_outlier"],
                "Video nhiều view nhất": r["best_title"],
                "View": r["best_views"],
                "Link video": f"https://www.youtube.com/watch?v={r['best_video_id']}",
            }
            for r in shown
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={
            "Link": st.column_config.LinkColumn(display_text="Mở kênh"),
            "Link video": st.column_config.LinkColumn(display_text="Xem"),
            "Hệ số cao nhất": st.column_config.NumberColumn(format="%.1f×"),
        },
    )
    st.download_button(
        "⬇️ Tải CSV", df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"kenh_{run['id']}.csv", mime="text/csv", key=f"{key}_allch_csv",
    )


def _render_small_channels(run: dict, key: str) -> None:
    cards = run["result"]["small_boom_channels"]
    if run["result"]["suggest_sub_niche"]:
        st.warning("Dưới 3 kênh nhỏ đang nổ: nên đào ngách con (thêm một bộ lọc đối tượng / vấn đề / góc kể).")
    if not cards:
        return
    checked = set(run["checked_channels"])
    notes = run.get("channel_notes") or {}
    card_ids = {c["channel_id"] for c in cards}
    opened = (checked | {cid for cid, n in notes.items() if n.get("reup")}) & card_ids
    with_notes = {cid for cid, n in notes.items() if n.get("note")} & card_ids
    st.caption(
        "Mở ít nhất 3 kênh xem họ tự làm nội dung hay re-up, ghi điểm lặp lại (góc kể, thời lượng, kiểu ảnh bìa). "
        f"Đã mở: **{len(opened)}/3** · có nhận xét: **{len(with_notes)}/3**. Kênh re-up dù nổ cũng không phải mẫu để học."
    )

    def _save_note(channel_id: str) -> None:
        run_repo.set_channel_note(
            get_conn(),
            run["id"],
            channel_id,
            note=st.session_state[f"{key}_note_{channel_id}"],
            reup=st.session_state[f"{key}_reup_{channel_id}"],
        )

    def _toggle(channel_id: str, widget_key: str) -> None:
        current = set(run_repo.get(get_conn(), run["id"])["checked_channels"])
        current.add(channel_id) if st.session_state[widget_key] else current.discard(channel_id)
        run_repo.set_checked_channels(get_conn(), run["id"], sorted(current))

    cols = st.columns(3)
    for i, c in enumerate(cards):
        with cols[i % 3].container(border=True):
            young = " · 🌱 kênh trẻ" if c["young"] else ""
            st.markdown(f"**[{c['title']}](https://www.youtube.com/channel/{c['channel_id']})**")
            st.caption(f"{fmt_num(c['subs'])} sub · {c['age_months']} tháng · {c['video_count']} video{young}")
            st.markdown(
                f"[{c['best_title'][:80]}](https://www.youtube.com/watch?v={c['best_video_id']}) — "
                f"{fmt_num(c['best_views'])} view, **{c['best_outlier']:g}×**"
            )
            wkey = f"{key}_chk_{c['channel_id']}"
            st.checkbox(
                "Đã mở xem, kênh tự làm nội dung",
                value=c["channel_id"] in checked,
                key=wkey,
                on_change=_toggle,
                args=(c["channel_id"], wkey),
            )
            n = notes.get(c["channel_id"], {})
            st.checkbox(
                "Kênh re-up / dùng lại nội dung người khác",
                value=bool(n.get("reup")),
                key=f"{key}_reup_{c['channel_id']}",
                on_change=_save_note,
                args=(c["channel_id"],),
            )
            st.text_input(
                "Điểm lặp lại",
                value=n.get("note", ""),
                placeholder="góc kể, thời lượng, kiểu ảnh bìa…",
                key=f"{key}_note_{c['channel_id']}",
                on_change=_save_note,
                args=(c["channel_id"],),
            )


def _render_format_split(r: dict) -> None:
    fs = r.get("format_split")
    if not fs:
        st.write("Lần đào này chỉ một định dạng. Chọn \"Mọi độ dài\" để so Dài / Shorts.")
        return
    c1, c2 = st.columns(2)
    for col, fmt, name in ((c1, "long", "Video dài (≥ 4 phút)"), (c2, "shorts", "Shorts (≤ 3 phút)")):
        s = fs[fmt]
        col.markdown(f"**{name}**")
        col.metric("Số video", s["n"])
        col.metric("Trung vị view", fmt_num(s["median_views"]))
        col.metric("Video vượt trội", s["outliers"])
    if fs["fit_label"]:
        st.success(fs["fit_label"])
        st.caption(reading.format_advice(fs["fit"]))
    else:
        st.caption("Chưa có định dạng nào thắng rõ (cần thắng cả trung vị lẫn số video vượt trội).")
    st.caption("Đừng so trực tiếp view Shorts với video dài; để đọc đúng, đào lại riêng định dạng bạn định làm.")


def _render_format_redig(run: dict, key: str) -> None:
    """Bài 2.2: khi đã chọn định dạng, đào lại riêng (dài từ 4 phút, Shorts dưới 3 phút) và so điểm."""
    from yt_niche.services.fetch import DigParams
    from yt_niche.services.runs import dig

    r = run["result"]
    latest: dict[str, dict] = {}
    for v in run_repo.format_variants(get_conn(), run):
        latest.setdefault(v["fmt"], v)
    if len(latest) > 1:
        st.markdown("**Điểm cùng chủ đề theo định dạng** (lần đào mới nhất mỗi định dạng)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Định dạng": report.FMT_LABEL[f],
                        "Điểm": round(v["score"]),
                        "Tin cậy": v["confidence"],
                        "Ngày đào": v["created_at"][:10],
                        "#": v["id"],
                    }
                    for f, v in latest.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    fs = r.get("format_split")
    if r["params"]["fmt"] != "any" or not fs or not fs["fit"] or fs["fit"] in latest:
        return
    label = report.FMT_LABEL[fs["fit"]]
    if st.button(
        f"⛏️ Đào lại riêng {label} để so điểm (≈ {EST_UNITS_PER_DIG} unit)", key=f"{key}_fmtdig", disabled=not cfg.api_key()
    ):
        params = DigParams(**{**r["params"], "fmt": fs["fit"]})
        with st.spinner(f"Đang đào riêng {label}…"):
            new_id = handle_youtube_errors(
                dig,
                get_conn(),
                params,
                category=run["category"],
                affiliates=run["affiliates"],
                parent_id=run.get("parent_id"),
                filter_type=run.get("filter_type") or "",
            )
        if new_id:
            st.session_state["current_run"] = new_id
            st.rerun()


def _render_trends(run: dict) -> None:
    t = run["result"]["trends"]
    if t["class"]:
        st.markdown(f"**{t['class'].capitalize()}**, {t['reason']}")
        st.caption(t["advice"])
    else:
        st.write(t["reason"])
    trends_raw = (run.get("raw") or {}).get("trends") or _load_raw_trends(run["id"])
    series = (trends_raw or {}).get("series") or {}
    tabs = st.tabs(["5 năm", "12 tháng", "3 tháng"])
    for tab, name in zip(tabs, ("5y", "12m", "3m")):
        with tab:
            points = series.get(name) or []
            if points:
                df = pd.DataFrame(points, columns=["Ngày", "Mức quan tâm"]).set_index("Ngày")
                st.line_chart(df, height=220)
            else:
                st.caption("Không có dữ liệu.")
    if derived := [trends_source.TIMEFRAME_LABELS[n] for n in (trends_raw or {}).get("derived") or []]:
        st.caption(f"Chuỗi {', '.join(derived)} suy ra từ đoạn cuối của chuỗi 5 năm (theo tuần) để đỡ tốn lượt gọi Google.")
    if (trends_raw or {}).get("provider") == "dataforseo":
        st.caption("Dữ liệu Trends lấy qua DataForSEO vì Google đang chặn máy bạn.")
    if not trends_raw or trends_raw.get("errors"):
        if msg := trends_source.describe_errors((trends_raw or {}).get("errors")):
            st.warning(msg)
        until = trends_source.cooldown_until(get_conn())
        backup = until and dfs.available(get_conn(), "dig")
        label = (
            "🔄 Lấy lại Trends qua DataForSEO (≈ $0.009)" if backup
            else "🔄 Lấy lại Google Trends" + (f" (sau {until:%H:%M})" if until else "")
        )
        if st.button(label, key=f"trends_refetch_{run['id']}", disabled=bool(until) and not backup, help="Không tốn quota YouTube"):
            with st.spinner("Đang lấy Google Trends…"):
                got = refetch_trends(get_conn(), run["id"])
            if got["errors"]:
                st.session_state[f"trends_msg_{run['id']}"] = trends_source.describe_errors(got["errors"])
            st.rerun()
        if msg := st.session_state.pop(f"trends_msg_{run['id']}", None):
            st.error(msg)


def _load_raw_trends(run_id: int) -> dict | None:
    run = run_repo.get(get_conn(), run_id, with_raw=True)
    return run["raw"].get("trends") if run else None


def _render_actions(run: dict, key: str) -> None:
    conn = get_conn()
    r = run["result"]
    st.divider()
    c1, c2 = st.columns(2)
    c1.download_button(
        "⬇️ Xuất Excel",
        data=report.to_excel(run),
        file_name=f"dao_ngach_{run['id']}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key}_xlsx",
    )
    shortlisted = c2.toggle(
        "Đưa vào bảng chấm",
        value=bool(run["shortlisted"]),
        disabled=not r["can_shortlist"],
        help="Độ tin cậy thấp thì không được đưa vào bảng chấm." if not r["can_shortlist"] else None,
        key=f"{key}_short",
    )
    if shortlisted != bool(run["shortlisted"]):
        run_repo.set_shortlisted(conn, run["id"], shortlisted)

    with st.expander("📋 Prompt cho Claude / ChatGPT"):
        style = st.text_input(
            "Kiểu kênh dự định", placeholder="vd. không lộ mặt, giọng AI, video 8–12 phút", key=f"{key}_style"
        )
        for name, title in report.PROMPTS.items():
            st.markdown(f"**{title}**")
            prompt_box(report.build_prompt(name, run, style))


def prompt_box(prompt: str) -> None:
    """Prompt để copy sang AI bên ngoài; bật AI ở Cài đặt → AI thì có thêm nút chạy ngay trong app."""
    from yt_niche.services import ai

    st.code(prompt, language="markdown")
    conn = get_conn()
    if not ai.enabled(conn, "prompts"):
        return
    answer = ai.cached_answer(conn, prompt)
    if st.button("🔄 Chạy lại bằng AI" if answer else "🤖 Chạy bằng AI", key="aip_" + hashlib.sha1(prompt.encode()).hexdigest()[:16]):
        with st.spinner("AI đang trả lời…"):
            try:
                answer = ai.run_prompt(conn, prompt)
            except ai.AIError as e:
                st.error(str(e))
    if answer:
        with st.container(border=True):
            st.markdown(answer)


def run_label(r: dict) -> str:
    return (
        f"{r['topic']} · {market_label(r['region'])} · {report.FMT_LABEL[r['fmt']]} · {r['window_days']} ngày · "
        f"{r['created_at'][:10]} · {r['score']:.0f} điểm (#{r['id']})"
    )


def handle_youtube_errors(fn, *args, **kwargs):
    """Gọi fn và hiện lỗi YouTube thân thiện; trả về None nếu lỗi."""
    from yt_niche.sources.youtube import QuotaExceeded, YouTubeError

    try:
        return fn(*args, **kwargs)
    except QuotaExceeded:
        st.error("Hết quota YouTube hôm nay. Quota reset lúc 0h giờ Thái Bình Dương (14h–15h giờ VN).")
    except YouTubeError as e:
        st.error(f"Lỗi YouTube API: {e}")
    return None
