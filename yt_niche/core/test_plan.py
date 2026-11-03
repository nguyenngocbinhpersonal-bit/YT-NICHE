"""Kế hoạch 10 video thử và luật quyết định ở mốc video 5 và 10 (bài 2.5)."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from yt_niche import config as cfg
from yt_niche.core.stats import median, slope
from yt_niche.core.trends_classify import FALLING

N_VIDEOS = 10
N_PILLARS = 3
MIN_PER_PILLAR = 3
TITLE_MAX = 70
REVIEW_AT = 5
DECIDE_AT = 10
MIN_FOR_TREND = 3  # cần ít nhất 3 video đã đo mới nói "tăng dần"

KEEP, ADJUST, SWITCH = "GIỮ", "CHỈNH", "CHUYỂN"

# Tín hiệu nên bỏ ngách dù có lượt xem (người dùng tự tick).
DROP_SIGNALS = {
    "trend_and_booms_gone": "Google Trends 1 năm giảm đều và kênh nhỏ nổ biến mất khi đào lại",
    "demonetized": "Liên tục bị giới hạn quảng cáo hoặc phải lách bản quyền",
    "template_only": "Chỉ làm được bằng cách lặp một khuôn y hệt",
    "no_money_path": "Không có đường ra tiền trong 12 tháng và không có sản phẩm để giới thiệu",
    "hate_it": "Bạn ghét làm ngách này",
}

MEASURE_FIELDS = ("impressions", "ctr", "views", "avd_seconds", "retention", "new_subs")


# ---------------------------------------------------------------------------
# Lập kế hoạch
# ---------------------------------------------------------------------------


def schedule(start: date, per_week: int, n: int = N_VIDEOS) -> list[date]:
    """Rải đều n video, per_week video mỗi tuần, bắt đầu từ start."""
    return [start + timedelta(days=round(i * 7 / per_week)) for i in range(n)]


def default_pillar_order(pillars: list[str]) -> list[str]:
    """Xoay vòng 3 trụ → 4 / 3 / 3 video."""
    return [pillars[i % len(pillars)] for i in range(N_VIDEOS)]


def plan_issues(pillars: list[str], videos: list[dict]) -> list[str]:
    issues = []
    names = [p.strip() for p in pillars if p and p.strip()]
    if len(names) != N_PILLARS or len(set(names)) != N_PILLARS:
        issues.append(f"Cần đúng {N_PILLARS} trụ nội dung khác nhau")
    counts = Counter(v.get("pillar") for v in videos)
    for p in names:
        if counts[p] < MIN_PER_PILLAR:
            issues.append(f"Trụ \"{p}\" mới có {counts[p]} video (cần ít nhất {MIN_PER_PILLAR})")
    empty = [v["idx"] for v in videos if not (v.get("title") or "").strip()]
    if empty:
        issues.append(f"Video chưa có tiêu đề: {', '.join(map(str, empty))}")
    long = [v["idx"] for v in videos if len(v.get("title") or "") > TITLE_MAX]
    if long:
        issues.append(f"Tiêu đề dài hơn {TITLE_MAX} ký tự: video {', '.join(map(str, long))}")
    return issues


# ---------------------------------------------------------------------------
# Đọc số sau 7 ngày
# ---------------------------------------------------------------------------


def measured(videos: list[dict]) -> list[dict]:
    return sorted((v for v in videos if v.get("views") is not None), key=lambda v: v["idx"])


def diagnose(videos: list[dict]) -> dict:
    m = measured(videos)
    med_views = median(v["views"] for v in m)
    standouts = [
        v for v in m if len(m) >= MIN_FOR_TREND and med_views and v["views"] >= cfg.TEST_STANDOUT_RATIO * med_views
    ]
    imp = median(v.get("impressions") for v in m)
    ctr = median(v.get("ctr") for v in m)
    ret = median(v.get("retention") for v in m)
    avd = [v["avd_seconds"] for v in m if v.get("avd_seconds") is not None]
    views = [v["views"] for v in m]
    return {
        "n": len(m),
        "median_views": med_views,
        "standouts": standouts,
        "median_impressions": imp,
        "median_ctr": ctr,
        "median_retention": ret,
        "avd_up": len(avd) >= MIN_FOR_TREND and slope(avd) > 0,
        "views_up": len(views) >= MIN_FOR_TREND and slope(views) > 0,
        "asks_more": sum(1 for v in m if v.get("asks_more")),
        "impressions_low": imp is not None and imp < cfg.TEST_IMPRESSIONS_LOW,
        "ctr_low": ctr is not None and ctr < cfg.TEST_CTR_LOW,
        "retention_low": ret is not None and ret < cfg.TEST_RETENTION_LOW,
    }


def fix_actions(diag: dict) -> list[str]:
    actions = []
    if diag["impressions_low"]:
        actions.append(
            f"Hiển thị thấp (trung vị {diag['median_impressions']:,.0f} < {cfg.TEST_IMPRESSIONS_LOW}): kiểm tra tiêu đề "
            "có chứa từ khoá người ta thật sự tìm không; đặt từ khoá chính lên đầu tiêu đề."
        )
    if diag["ctr_low"]:
        actions.append(
            f"CTR thấp (trung vị {diag['median_ctr']:.1f}% < {cfg.TEST_CTR_LOW:g}%): sửa tiêu đề và ảnh bìa; "
            "giữ ngách nếu người xem vẫn ở lại lâu."
        )
    if diag["retention_low"]:
        actions.append(
            f"Người xem rời sớm (giữ chân {diag['median_retention']:.0f}% < {cfg.TEST_RETENTION_LOW:g}%): sửa hook và 30 giây đầu."
        )
    return actions


def review_at_5(videos: list[dict]) -> dict:
    diag = diagnose(videos)
    if diag["n"] < REVIEW_AT:
        return {"ready": False, "diag": diag, "actions": [], "message": f"Đã đo {diag['n']}/{REVIEW_AT} video; chưa tới mốc xem lại."}
    actions = fix_actions(diag) or ["Chưa thấy điểm yếu rõ: giữ nhịp đăng, thử biến thể tiêu đề / ảnh bìa ở video tiếp theo."]
    return {"ready": True, "diag": diag, "actions": actions, "message": "Mốc video 5: sửa tiêu đề, ảnh bìa, hook trước khi làm tiếp."}


def decide_at_10(videos: list[dict], *, fixes_applied: bool, out_of_ideas: bool) -> dict:
    diag = diagnose(videos)
    if out_of_ideas:
        return {"ready": True, "decision": SWITCH, "diag": diag, "reasons": ["Cạn ý trước video 10"], "actions": ["Chuyển sang ngách dự phòng."]}
    if diag["n"] < DECIDE_AT:
        msg = f"Đã đo {diag['n']}/{DECIDE_AT} video. Đừng kết luận ngách hỏng khi mới làm vài video."
        return {"ready": False, "decision": None, "diag": diag, "reasons": [msg], "actions": []}

    keep_signals = []
    if diag["standouts"]:
        ids = ", ".join(str(v["idx"]) for v in diag["standouts"])
        keep_signals.append(f"{len(diag['standouts'])} video vượt rõ mức chung (video {ids})")
    if diag["avd_up"]:
        keep_signals.append("thời lượng xem trung bình tăng dần")
    if diag["asks_more"]:
        keep_signals.append(f"{diag['asks_more']} video có bình luận hỏi thêm chủ đề cùng ngách")

    if diag["standouts"] and len(keep_signals) >= 2:
        pillar = Counter(v.get("pillar") for v in diag["standouts"]).most_common(1)[0][0]
        return {
            "ready": True,
            "decision": KEEP,
            "diag": diag,
            "reasons": keep_signals,
            "actions": [f"Dồn thêm video vào trụ \"{pillar}\"."] if pillar else [],
        }

    no_movement = not diag["standouts"] and not diag["views_up"]
    if fixes_applied and (diag["impressions_low"] or no_movement):
        reasons = []
        if diag["impressions_low"]:
            reasons.append("đã chỉnh mà hiển thị vẫn gần như không có")
        if no_movement:
            reasons.append("không video nào nhích")
        return {
            "ready": True,
            "decision": SWITCH,
            "diag": diag,
            "reasons": reasons,
            "actions": [
                "Trước khi đổi, tách lỗi chủ đề khỏi lỗi cách làm (tiêu đề, ảnh bìa, hook, giọng đọc).",
                "Chuyển sang ngách dự phòng, giữ phong cách kênh nếu dự phòng gần khán giả.",
            ],
        }

    actions = fix_actions(diag)
    if not fixes_applied:
        actions.append("Chưa đánh dấu đã chỉnh ở mốc video 5: chỉnh theo gợi ý rồi đo thêm trước khi tính chuyện chuyển ngách.")
    return {
        "ready": True,
        "decision": ADJUST,
        "diag": diag,
        "reasons": keep_signals or ["chưa đủ tín hiệu để giữ, nhưng cũng chưa đủ căn cứ để chuyển"],
        "actions": actions or ["Thử biến thể tiêu đề / ảnh bìa / hook cho trụ đang tốt nhất."],
    }


def auto_drop_hints(history: list[dict]) -> list[str]:
    """history: các lần đào cùng thiết lập của ngách chính, mới nhất trước."""
    if not history:
        return []
    hints = []
    newest = history[0]["result"]
    if newest["trends"].get("class") == FALLING:
        hints.append("Lần đào mới nhất: Google Trends đang rơi")
    if len(history) >= 2:
        before, now = len(history[1]["result"]["small_boom_channels"]), len(newest["small_boom_channels"])
        if before and now == 0:
            hints.append(f"Kênh nhỏ nổ biến mất khi đào lại ({before} → 0)")
    return hints
