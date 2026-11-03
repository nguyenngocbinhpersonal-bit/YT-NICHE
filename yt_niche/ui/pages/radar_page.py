"""Trang Radar từ khoá: quét danh mục Google Trends rải đều trong ngày, lọc từ khoá tiềm năng."""

import pandas as pd
import streamlit as st

from yt_niche import config as cfg
from yt_niche.core import radar as rd
from yt_niche.services import ai, radar
from yt_niche.services.fetch import DigParams
from yt_niche.services.runs import dig
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import trends
from yt_niche.ui.common import get_conn, handle_youtube_errors, market_label

st.title("📡 Radar từ khoá")
st.caption(
    "Hạt giống lấy từ cây danh mục chính thức của Google Trends (truy vấn hàng đầu và đang tăng của từng danh mục, "
    "từng thị trường), bỏ các danh mục tin tức, người nổi tiếng, sự kiện. Mỗi từ khoá mới qua 3 lớp lọc: loại thực thể "
    "của Google và AI (người, bài hát, show, đội bóng, sự kiện → loại), lượng tìm Google Trends 30 ngày gần nhất trên "
    "YouTube Search và Google Search (gần như không ai tìm ở một trong hai → loại), rồi điểm từ khoá YouTube. "
    "Các lượt rải đều trong 24 giờ để Google không chặn (429)."
)
conn = get_conn()
# Cột AI hiện khi đã có từ khoá được AI phân loại (bật ở Cài đặt → AI).
show_ai = conn.execute("SELECT 1 FROM radar_keyword WHERE ai_type IS NOT NULL LIMIT 1").fetchone() is not None

try:
    tree = trends.category_tree(conn)
    cat_names = {c["id"]: c["name"] for c in rd.flatten(tree)}
except trends.Throttled:
    tree, cat_names = None, {}

# ---------------------------------------------------------------------------
# Trạng thái chạy nền
# ---------------------------------------------------------------------------

stats = radar.stats(conn)
c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Lượt Trends hôm nay", f"{stats['jobs_today']}/{stats['jobs_per_day']}",
    help=f"Nhịp hiện tại: {stats['interval_s']} giây/lượt. App tự nhanh thêm {cfg.RADAR_SPEEDUP_PCT}% sau mỗi {cfg.RADAR_SPEEDUP_EVERY} lượt "
         f"liền không bị chặn (nhanh nhất {cfg.RADAR_MIN_INTERVAL_S} giây), bị Google chặn thì chậm gấp đôi.",
)
c2.metric("Lượt kế tiếp", f"{stats['next_due'].astimezone():%H:%M}" if stats["next_due"] else "ngay")
c3.metric(
    "Một vòng quét đủ phạm vi", f"≈ {stats['cycle_days']:g} ngày" if stats["cycle_days"] is not None else "—",
    help=(
        f"{stats['pairs'] or 0} cặp thị trường × danh mục, mỗi cặp ≈ {stats['per_pair']:g} lượt (quét + kiểm tra từ khoá mới), "
        f"{stats['jobs_per_day']} lượt/ngày. {stats['slow_pairs']} cặp đang quét thưa (không có dữ liệu: "
        f"{cfg.RADAR_EMPTY_RESCAN_DAYS} ngày/lần; chưa ra từ khoá đạt sau 3 lần: {cfg.RADAR_BARREN_RESCAN_DAYS} ngày/lần). "
        f"Đã quét trong 24 giờ qua: {stats['fresh_pairs']}."
    ),
)
c4.metric("Quota YouTube Radar hôm nay", f"{stats['yt_units_today']}/{cfg.RADAR_YT_UNITS_PER_DAY}")
if stats["cycle_days_at_max"] is not None:
    if stats["hours_to_max"] > 0:
        st.info(
            f"⚡ Ở nhịp nhanh nhất ({cfg.RADAR_MIN_INTERVAL_S} giây/lượt, {stats['max_jobs_per_day']} lượt/ngày) một vòng quét "
            f"chỉ còn **≈ {stats['cycle_days_at_max']:g} ngày**. Nếu Google không chặn lần nào, Radar đạt nhịp này sau "
            f"**≈ {stats['hours_to_max']:g} giờ** (đang {stats['interval_s']} giây/lượt). Bị chặn thì chậm lại gấp đôi rồi tăng tốc lại."
        )
    else:
        st.success(f"⚡ Radar đang chạy ở nhịp nhanh nhất: {cfg.RADAR_MIN_INTERVAL_S} giây/lượt.")
if until := trends.cooldown_until(conn):
    if why := dfs.unavailable_reason(conn, "radar"):
        st.warning(f"Google Trends đang tạm chặn: Radar nghỉ đến {until:%H:%M} rồi tự chạy tiếp, chậm lại gấp đôi ({why}).")
    else:
        du = dfs.usage_today(conn)
        st.info(f"Google Trends đang tạm chặn đến {until:%H:%M}: Radar tạm lấy dữ liệu qua DataForSEO "
                f"(hôm nay {du['tasks']} lượt, ${du['cost']:.3f}).")
if stats["unscanned"]:
    st.caption(
        f"Còn {stats['unscanned']} cặp chưa quét lần nào (≈ {stats['first_sweep_days']:g} ngày ở nhịp hiện tại). Vòng quét "
        "tự ngắn lại theo thời gian: nhịp nhanh dần khi Google không chặn, cặp không có dữ liệu hoặc không ra từ khoá đạt "
        "được quét thưa, loại thực thể dùng chung giữa các nước. Muốn nhanh hơn nữa: bớt thị trường / danh mục ở tab Phạm vi quét."
    )

task = radar.task_status()
b1, b2, _ = st.columns([2, 1, 2])
if task is not None:
    if task:
        b1.success("Chạy nền bằng Windows Task Scheduler: **đang bật** (chạy cả khi app tắt, máy phải bật).")
        if b2.button("Tắt chạy nền"):
            st.toast(radar.disable_task())
            st.rerun()
    else:
        b1.info("Chạy nền: chỉ khi app đang mở. Bật Task Scheduler để quét cả khi app tắt.")
        if b2.button("Bật chạy nền", type="primary"):
            st.toast(radar.enable_task())
            st.rerun()
if st.button("▶️ Chạy 1 lượt ngay", help="Bỏ qua giờ hẹn; vẫn tôn trọng thời gian nghỉ khi Google chặn"):
    with st.spinner("Đang chạy một lượt…"):
        result = radar.tick(conn, force=True)
    st.toast(f"{result['action']}: {result['detail']}")
    st.rerun()

# ---------------------------------------------------------------------------
# Bảng từ khoá
# ---------------------------------------------------------------------------

KIND = {"rising": "đang tăng", "top": "hàng đầu"}
# Link xem Google Trends 12 tháng (YouTube Search) của từ khoá, đúng thị trường.
LINKS = {
    "Trends YouTube": st.column_config.LinkColumn(display_text="▶️ Xem", help="Google Trends 12 tháng, YouTube Search, đúng thị trường"),
}


def _table(rows: list[dict], extra: bool = False) -> pd.DataFrame:
    out = []
    for r in rows:
        yt = r.get("yt") or {}
        out.append(
            {
                "⭐": "⭐" if (r["potential"] or 0) >= cfg.RADAR_GOOD_MIN else "",
                "Từ khoá": r["keyword"],
                "Trends YouTube": trends.explore_url(r["keyword"], r["region"], "youtube", trends.TIMEFRAMES["12m"]),
                **({"Nghĩa (AI)": r.get("ai_vi") or "", "Loại (AI)": ai.KEYWORD_TYPES.get(r.get("ai_type"), ("",))[0]} if show_ai else {}),
                "Thị trường": market_label(r["region"]),
                "Danh mục": cat_names.get(r["category_id"], r["category_id"]),
                "Nguồn": f"{KIND[r['kind']]} +{r['value']:,}%" if r["kind"] == "rising" else f"{KIND[r['kind']]} ({r['value']})",
                "Số ngày gặp": r["seen_days"],
                "YouTube": f"{yt['score']:.0f} · {yt['label']}" + (" · ít dữ liệu" if yt.get("low_data") else "") if yt else "",
                "Tiềm năng": r["potential"],
                "Trạng thái": rd.STATUS_LABEL[r["status"]],
                "Thấy lần đầu": r["first_seen"][:10],
                **({"Lý do": r["reason"], "Loại thực thể": r["entity_type"] or ""} if extra else {}),
            }
        )
    return pd.DataFrame(out)


# Một bộ lọc cho mọi bảng kết quả, chỉ gồm thị trường đang bật ở Cài đặt → Thị trường.
picked = st.multiselect(
    "Thị trường", cfg.ENABLED_MARKETS, format_func=market_label,
    placeholder=f"Tất cả {len(cfg.ENABLED_MARKETS)} thị trường đang bật",
    help="Danh sách lấy từ Cài đặt → Thị trường; thị trường đã tắt không hiện ở đây và Radar không quét nữa.",
)
regions = picked or list(cfg.ENABLED_MARKETS)

tab_good, tab_pending, tab_out, tab_scope, tab_log = st.tabs(
    ["⭐ Từ khoá tiềm năng", "⏳ Chờ kiểm tra", "🚫 Đã loại", "⚙️ Phạm vi quét", "📜 Nhật ký"]
)

with tab_good:
    min_score = st.slider("Tiềm năng từ", 0, 100, 0)
    rows = [r for r in radar.keywords(conn, [rd.PASSED, rd.WATCHING], regions) if (r["potential"] or 0) >= min_score]
    if not rows:
        st.info("Chưa có từ khoá nào qua lọc. Radar cần vài giờ chạy nền để có kết quả đầu tiên.")
    else:
        st.caption(
            f"Điểm tiềm năng = đà tăng × {cfg.RADAR_WEIGHTS['momentum']:.0%} + độ đều 30 ngày × {cfg.RADAR_WEIGHTS['durability']:.0%} "
            f"+ YouTube × {cfg.RADAR_WEIGHTS['youtube']:.0%} + số ngày lặp lại × {cfg.RADAR_WEIGHTS['persistence']:.0%} "
            "(chưa chấm YouTube thì chia lại cho 3 phần kia). Radar tự chấm YouTube cho từ khoá cao điểm nhất trong ngân "
            "sách quota mỗi ngày. Chọn dòng để theo dõi, bỏ qua, chấm YouTube hoặc đào ngách."
        )
        event = st.dataframe(
            _table(rows), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="radar_good",
            column_config={"Tiềm năng": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"), **LINKS},
        )
        chosen = [rows[i] for i in event.selection.rows]
        items = [(r["region"], r["keyword"]) for r in chosen]
        a1, a2, a3, a4 = st.columns(4)
        if a1.button("👁️ Theo dõi", disabled=not chosen):
            radar.set_status(conn, items, rd.WATCHING)
            st.rerun()
        if a2.button("🙈 Bỏ qua (không hiện lại)", disabled=not chosen):
            radar.set_status(conn, items, rd.IGNORED)
            st.rerun()
        if a3.button(f"🔎 Chấm YouTube (≈{102 * len(chosen)} unit)", disabled=not chosen or len(chosen) > 5 or not cfg.api_key()):
            with st.spinner("Đang chấm…"):
                for region, kw in items:
                    if handle_youtube_errors(radar.score_youtube, conn, region, kw) is None:
                        break
            st.rerun()
        if a4.button("⛏️ Đào ngách (≈320 unit)", disabled=len(chosen) != 1 or not cfg.api_key()):
            r = chosen[0]
            params = DigParams(topic=r["keyword"], region=r["region"], language=cfg.MARKETS[r["region"]][0])
            with st.spinner(f"Đang đào \"{r['keyword']}\"…"):
                run_id = handle_youtube_errors(dig, conn, params, category=None)
            if run_id:
                st.session_state["current_run"] = run_id
                st.switch_page("pages/dig.py")

with tab_pending:
    rows = radar.keywords(conn, [rd.PENDING], regions)
    st.caption(f"{len(rows)} từ khoá mới đang chờ kiểm tra loại thực thể và hình dạng Trends (mỗi lượt kiểm tra 1 từ).")
    if rows:
        st.dataframe(_table(rows), hide_index=True, width="stretch", column_config=LINKS)

with tab_out:
    rows = radar.keywords(conn, [rd.EXCLUDED, rd.IGNORED], regions)
    st.caption("Từ khoá đã loại kèm lý do, để bạn kiểm tra bộ lọc có loại nhầm không. Từ khoá đã loại không quét lại.")
    if rows:
        event = st.dataframe(
            _table(rows, extra=True), hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
            key="radar_out", column_config=LINKS,
        )
        picked = [(rows[i]["region"], rows[i]["keyword"]) for i in event.selection.rows]
        if st.button("↩️ Đưa lại vào hàng chờ kiểm tra", disabled=not picked):
            radar.set_status(conn, picked, rd.PENDING)
            st.rerun()

with tab_scope:
    if tree is None:
        st.warning("Chưa tải được cây danh mục Google Trends (Google đang tạm chặn). Thử lại sau.")
    else:
        scope = radar.get_scope(conn)
        all_markets = st.checkbox(
            "Quét mọi thị trường đang bật ở Cài đặt → Thị trường (tự theo khi bạn bật / tắt thị trường)",
            value=scope["all_markets"],
        )
        markets = st.multiselect(
            "Thị trường", cfg.ENABLED_MARKETS, default=[m for m in scope["markets"] if m in cfg.ENABLED_MARKETS],
            format_func=market_label, disabled=all_markets,
        )
        options = rd.selectable_categories(tree)
        labels = {c["id"]: ("· " * (c["depth"] - 1)) + c["name"] for c in options}
        cats = st.multiselect(
            "Danh mục Google Trends (nhóm gốc và danh mục con)", [c["id"] for c in options],
            default=[c for c in scope["categories"] if c in labels], format_func=lambda i: labels[i],
        )
        st.caption(
            "Luôn bỏ: " + ", ".join(rd.EXCLUDED_CATEGORIES.values()) + ". Chọn nhóm gốc thì gồm cả danh mục con của nó; "
            "Nghệ thuật & Giải trí mặc định quét theo danh mục con để tránh tin người nổi tiếng."
        )
        block = st.text_area("Từ chặn (mỗi dòng một từ; truy vấn chứa từ này bị bỏ)", "\n".join(scope["blocklist"]))
        n_markets = len(cfg.ENABLED_MARKETS) if all_markets else len(markets)
        st.caption(f"{n_markets} thị trường × {len(cats)} danh mục = **{n_markets * len(cats)} cặp** cần quét mỗi vòng.")
        s1, s2 = st.columns(2)
        if s1.button("Lưu phạm vi", type="primary"):
            radar.save_scope(conn, markets, cats, block.splitlines(), all_markets)
            st.success("Đã lưu.")
            st.rerun()
        if s2.button("Về mặc định"):
            conn.execute("DELETE FROM radar_meta WHERE key = 'scope'")
            st.rerun()

with tab_log:
    st.subheader("📈 Nhịp quét (để hiệu chỉnh)")
    days = st.segmented_control("Khoảng", [1, 3, 7, 30, 180], default=7, format_func=lambda d: f"{d} ngày", key="pace_days") or 7
    rep = radar.pace_report(conn, days)
    if not rep["rows"]:
        st.caption("Chưa có lượt nào gọi Google trong khoảng này.")
    else:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Lượt có gọi Google", f"{rep['ticks']:,}", help=f"{rep['requests']:,} yêu cầu, trung bình {rep['req_per_tick']:g}/lượt")
        p2.metric("Bị chặn (429)", rep["blocks"],
                  help=f"Lượt phải nghỉ vì thử lại vẫn bị chặn. Thêm {rep['rescued']} lần 429 được cứu nhờ lấy cookie mới.")
        p3.metric("Khoảng cách thật (trung vị)", f"{rep['median_gap_s']} giây" if rep["median_gap_s"] else "—",
                  help="Giây thật giữa hai lượt có gọi Google. Lớn hơn nhịp đặt ra vì Task Scheduler gọi 2 phút/lần và nhịp có ±20% ngẫu nhiên.")
        p4.metric("Yêu cầu/giờ cao nhất chưa bị chặn", rep["max_ok_req_1h"])
        if rep["blocks"]:
            st.warning(
                f"Google chặn sớm nhất khi đã gửi **{rep['min_block_req_1h']} yêu cầu trong 60 phút**. Giữ dưới 80% mức đó với "
                f"{rep['req_per_tick']:g} yêu cầu/lượt thì nhịp nhanh nhất nên ≈ **{rep['suggested_min_interval']} giây** "
                f"(đang đặt {cfg.RADAR_MIN_INTERVAL_S} giây, sửa ở Cài đặt → Ngưỡng tính điểm → Radar từ khoá)."
            )
        elif rep["fastest_ok_interval"] is not None:
            st.success(
                f"Chưa bị chặn lần nào trong {days} ngày; nhịp nhanh nhất đã chạy là {rep['fastest_ok_interval']:g} giây/lượt, "
                f"tối đa {rep['max_ok_req_1h']} yêu cầu/giờ. Có thể thử hạ \"Radar: nhanh nhất\" dần (vd. −10 giây mỗi lần) và theo dõi tại đây."
            )
        df = pd.DataFrame(rep["rows"])
        df["at"] = pd.to_datetime(df["at"]).dt.tz_convert(None) + (pd.Timestamp.now() - pd.Timestamp.now("UTC").tz_localize(None))
        chart = df.set_index("at")[["interval_after", "gap_s"]].rename(
            columns={"interval_after": "Nhịp đặt (giây/lượt)", "gap_s": "Khoảng cách thật (giây)"})
        st.line_chart(chart, height=260)
        st.bar_chart(df.set_index("at")[["req_1h"]].rename(columns={"req_1h": "Yêu cầu trong 60 phút"}), height=200)
        if rep["blocks"]:
            st.markdown("**Các lần bị chặn**")
            st.dataframe(
                df[df["throttled"] == 1][["at", "interval_before", "gap_s", "req_1h", "streak"]].rename(columns={
                    "at": "Lúc", "interval_before": "Nhịp lúc đó (giây)", "gap_s": "Cách lượt trước (giây)",
                    "req_1h": "Yêu cầu trong 60 phút", "streak": "Lượt liền không bị chặn"}),
                hide_index=True, width="stretch",
            )
        st.download_button(
            "⬇️ Tải nhật ký nhịp (CSV)", df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"radar_pace_{days}d.csv", mime="text/csv",
        )
    st.subheader("📜 50 lượt gần nhất")
    if stats["log"]:
        st.dataframe(
            pd.DataFrame([{"Lúc": e["at"][:19].replace("T", " ") + " UTC", "Việc": e["action"], "Chi tiết": e["detail"]} for e in stats["log"]]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("Chưa có lượt nào.")
    st.caption(f"Lỗi của lượt chạy nền (nếu có) ghi ở {cfg.DATA_DIR / 'radar_errors.log'}.")
