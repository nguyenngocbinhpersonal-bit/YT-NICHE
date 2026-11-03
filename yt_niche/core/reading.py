"""Đọc kết quả một lần đào (bài 2.1, 2.2): phiếu 7 ô, lời khuyên theo vùng điểm, độ tin cậy, định dạng, bật kiếm tiền."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.confidence import HIGH, LOW, MEDIUM
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.text import no_space, word_count

# Điều kiện bật kiếm tiền quảng cáo YouTube (bài 2.1).
MONETIZATION = (
    "Để có quảng cáo: 1.000 người đăng ký kèm 4.000 giờ xem video dài trong 12 tháng, "
    "hoặc 1.000 người đăng ký kèm 10 triệu lượt xem Shorts trong 90 ngày. Affiliate không cần bật kiếm tiền."
)

FORMAT_LABEL = {LONG: "video dài", SHORTS: "Shorts"}


def zone_advice(score: float) -> str:
    """Ba vùng điểm và hành động (bài 2.2)."""
    if score >= cfg.LABEL_GREEN_MIN:
        return "Vào được; nên ưu tiên ngách con và từ khoá dài thay vì làm chung chung."
    if score >= cfg.LABEL_POTENTIAL_MIN:
        return "Cần góc khác biệt (đối tượng, thị trường, định dạng). Đào thêm 2–3 ngách con trước khi quyết."
    return "Nên tránh, hoặc đào ngách con hẹp hơn, hoặc đổi thị trường."


def confidence_advice(confidence: str) -> str:
    if confidence == LOW:
        return (
            "Mẫu còn ít: không kết luận được, bất kể điểm. Nới khoảng thời gian (vd. 30 → 90 ngày), "
            "dùng 2–4 từ cụ thể hơn hoặc đổi từ khoá rồi đào lại. Không được chốt ngách."
        )
    if confidence == MEDIUM:
        return "Tin cậy vừa: dùng được để so sánh, nhưng phải mở vài kênh thật trước khi chốt."
    return "Tin cậy cao: đủ mẫu để so và chốt."


ESTIMATE_NOTE = (
    "Số có dấu ≈ là ước tính (kênh ẩn sub, video ẩn view, kênh chưa đủ video để tính trung vị): "
    "chỉ dùng để nhìn hướng, không so chênh lệch vài phần trăm."
)


def format_advice(fit: str | None) -> str | None:
    if fit == LONG:
        return "Video dài nhiều video vượt trội hơn: làm video dài là chính, Shorts làm mồi."
    if fit == SHORTS:
        return (
            "Shorts áp đảo, nhưng tiền mỗi lượt xem của Shorts thấp hơn nhiều: tính tiền bằng affiliate "
            "hoặc mốc 10 triệu lượt xem Shorts trong 90 ngày; đừng kết luận Shorts kiếm nhiều tiền hơn chỉ vì view cao."
        )
    return None


def topic_warning(topic: str, language: str) -> str | None:
    """Chủ đề nên 2–4 từ cụ thể (tiếng Việt tính theo âm tiết nên cho phép đến 8)."""
    if no_space(language):
        return None
    n = word_count(topic, language)
    hi = 8 if language == "vi" else 4
    if n < 2:
        return "Chủ đề một từ thường quá rộng: dùng 2–4 từ cụ thể."
    if n > hi:
        return "Chủ đề quá dài dễ thành chủ đề video, không phải ngách: dùng 2–4 từ cụ thể."
    return None


def seven_cells(run: dict) -> dict:
    """Phiếu 7 ô (bài 2.2). Đủ 7 ô với độ tin cậy vừa hoặc cao mới đưa vào so sánh ở bài 2.4."""
    r = run["result"]
    params = r["params"]
    fs = r.get("format_split")
    if params["fmt"] in (LONG, SHORTS):
        fmt_value = f"đào riêng {FORMAT_LABEL[params['fmt']]}"
    elif fs:
        fmt_value = fs.get("fit_label") or "chưa định dạng nào thắng rõ"
    else:
        fmt_value = None
    trend = r["trends"].get("class")
    cells = [
        ("score", "Điểm + nhãn", f"{r['score']:.0f} · {r['labels'][0]}", True),
        ("confidence", "Độ tin cậy", f"{r['confidence']} ({r['n']} video)", r["confidence"] in (HIGH, MEDIUM)),
        (
            "bars",
            "4 thanh con",
            f"Nhu cầu {r['demand']:.0f} · Cơ hội {r['opportunity']:.0f} · Độ mới {r['freshness']:.0f} · Cạnh tranh {r['competition']:.0f}",
            True,
        ),
        ("outliers", "Video vượt trội", str(r["outlier_counts"]["total"]), True),
        ("small_channels", "Kênh nhỏ nổ", str(len(r["small_boom_channels"])), True),
        ("format", "Định dạng thắng", fmt_value or "chưa có", fmt_value is not None),
        ("trends", "Hình dạng Trends", trend or "chưa có Trends", trend is not None),
    ]
    rows = [{"key": k, "label": label, "value": v, "ok": ok} for k, label, v, ok in cells]
    missing = [c["label"] for c in rows if not c["ok"]]
    return {"cells": rows, "complete": not missing, "missing": missing}
