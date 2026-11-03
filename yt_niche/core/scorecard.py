"""Bảng chấm 10 tiêu chí để chốt ngách (bài 2.5): điểm chốt, cờ đỏ, chọn ngách chính / dự phòng."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from yt_niche.core.keyword_score import long_keyword_score
from yt_niche.core.money import RPM_HIGH, RPM_MEDIUM
from yt_niche.core.trends_classify import FAD, FALLING, SEASONAL, STABLE

TIE_GAP = 5  # chênh dưới 5 điểm coi như hoà
RED_FLAG_SCORE = 1
MIN_MAIN_SCORE = 2  # ngách chính không được có ô nào dưới 2
YOUNG_BOOM_MONTHS = 6  # "có kênh nổ dưới 6 tháng tuổi"
SENSITIVE_CATEGORIES = ("Tài chính", "Sức khỏe", "Tâm linh")


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: int
    guide: str
    auto: bool  # app tự gợi ý được từ số liệu đào


CRITERIA: list[Criterion] = [
    Criterion("demand", "Nhu cầu", 15, "Lượt xem trung vị; 5 = cao nhất trong các ngách đang so", True),
    Criterion("trend", "Xu hướng", 10, "Google Trends 1 năm / 5 năm; 5 = bền hoặc đi lên", True),
    Criterion("small_channels", "Kênh nhỏ đang nổ", 15, "5 = nhiều kênh nhỏ nổ, có kênh dưới 6 tháng tuổi", True),
    Criterion("competition", "Cạnh tranh (chấm ngược)", 10, "5 = ít kênh trên 1 triệu sub chiếm top", True),
    Criterion("ad_price", "Giá quảng cáo", 10, "Theo thị trường và chủ đề (bảng RPM)", True),
    Criterion("money_other", "Tiền ngoài quảng cáo", 10, "Affiliate, sản phẩm riêng, sức mua; 5 = nhiều sản phẩm gắn tự nhiên", False),
    Criterion("production", "Sản xuất bền", 10, "Làm được 30+ video, công mỗi video vừa sức, tư liệu dồi dào", False),
    Criterion("policy_risk", "Rủi ro chính sách (chấm ngược)", 10, "Trừ điểm nếu dễ vướng bản quyền, nhạy cảm, y tế / tài chính", False),
    Criterion("passion", "Mức thích của bạn", 5, "Bạn có tự đọc / xem chủ đề này khi không ai trả tiền?", False),
    Criterion("long_keywords", "Từ khoá dài", 5, "5 = có từ 10 từ khoá dài trở lên", False),
]
CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}
DEFAULT_WEIGHTS = {c.key: c.weight for c in CRITERIA}


# ---------------------------------------------------------------------------
# Điểm chốt và luật chọn
# ---------------------------------------------------------------------------


def weights_error(weights: dict[str, int]) -> str | None:
    if set(weights) != set(DEFAULT_WEIGHTS):
        return "Thiếu tiêu chí trong bảng trọng số"
    if any(w < 0 for w in weights.values()):
        return "Trọng số không được âm"
    total = sum(weights.values())
    return None if total == 100 else f"Tổng trọng số phải bằng 100 (đang là {total})"


def final_score(weights: dict[str, int], scores: dict[str, int | None]) -> float | None:
    """Σ(trọng số × điểm 1–5) ÷ 5. None nếu còn ô chưa chấm (ô có trọng số 0 được bỏ qua)."""
    if any(scores.get(k) is None for k, w in weights.items() if w > 0):
        return None
    return round(sum(w * scores[k] for k, w in weights.items() if w > 0) / 5, 1)


def red_flags(weights: dict[str, int], scores: dict[str, int | None]) -> list[str]:
    return [k for k, w in weights.items() if w > 0 and scores.get(k) == RED_FLAG_SCORE]


def is_eligible(weights: dict[str, int], scores: dict[str, int | None]) -> bool:
    return all(scores.get(k) is not None and scores[k] >= MIN_MAIN_SCORE for k, w in weights.items() if w > 0)


def _tie_value(item: dict) -> int:
    return (item["scores"].get("production") or 0) + (item["scores"].get("money_other") or 0)


def _pick(candidates: list[dict]) -> tuple[dict | None, str]:
    """Điểm cao nhất; nếu ngách khác chênh < 5 điểm thì quyết bằng Sản xuất bền + Tiền ngoài quảng cáo."""
    if not candidates:
        return None, ""
    ranked = sorted(candidates, key=lambda x: x["final"], reverse=True)
    top = ranked[0]
    close = [c for c in ranked if top["final"] - c["final"] < TIE_GAP]
    if len(close) == 1:
        return top, f"điểm chốt cao nhất ({top['final']:g})"
    winner = max(close, key=lambda c: (_tie_value(c), c["final"]))
    names = ", ".join(f"{c['name']} {c['final']:g}" for c in close)
    return winner, (
        f"hoà điểm (chênh < {TIE_GAP}: {names}), quyết bằng Sản xuất bền + Tiền ngoài quảng cáo "
        f"= {_tie_value(winner)}"
    )


def decide(items: list[dict], weights: dict[str, int]) -> dict:
    """items: [{"id", "name", "scores": {criterion: 1–5 | None}}].

    Trả về bảng xếp hạng, ngách chính, ngách dự phòng và lý do. Chưa chấm đủ thì main/backup là None.
    """
    rows = []
    for it in items:
        final = final_score(weights, it["scores"])
        rows.append(
            {
                **it,
                "final": final,
                "red_flags": red_flags(weights, it["scores"]),
                "eligible": final is not None and is_eligible(weights, it["scores"]),
            }
        )
    ranking = sorted(rows, key=lambda r: (r["final"] is None, -(r["final"] or 0)))
    complete = all(r["final"] is not None for r in rows)
    result = {"ranking": ranking, "complete": complete, "main": None, "backup": None, "main_reason": "", "backup_reason": ""}
    if not complete:
        return result

    eligible = [r for r in rows if r["eligible"]]
    main, why = _pick(eligible)
    if main:
        result["main"], result["main_reason"] = main, why
        backup, why_b = _pick([r for r in eligible if r["id"] != main["id"]])
        result["backup"], result["backup_reason"] = backup, why_b
    return result


# ---------------------------------------------------------------------------
# Gợi ý điểm tự động từ kết quả đào
# ---------------------------------------------------------------------------


def _score_demand(median: float | None, best: float | None) -> tuple[int | None, str]:
    if not median or not best:
        return None, "không có trung vị view"
    # Cao nhất = 5; mỗi lần kém một nửa thì trừ 1 điểm.
    score = max(1, min(5, 5 - round(math.log2(best / median))))
    return score, f"trung vị {median:,.0f} view (cao nhất trong bảng: {best:,.0f})".replace(",", ".")


def _score_trend(trends: dict) -> tuple[int, str]:
    cls = trends.get("class")
    if cls == STABLE:
        score = 4 if trends.get("direction") == "đi xuống" else 5
    elif cls == SEASONAL:
        score = 4
    elif cls in (FAD, FALLING):
        score = 2
    else:
        return 3, "chưa có Trends"
    return score, f"Trends: {cls}" + (f", {trends['direction']}" if trends.get("direction") else "")


def _score_small_channels(cards: list[dict]) -> tuple[int, str]:
    n = len(cards)
    score = 5 if n >= 10 else 4 if n >= 5 else 3 if n >= 3 else 2 if n >= 1 else 1
    young = sum(1 for c in cards if c.get("age_months") is not None and c["age_months"] < YOUNG_BOOM_MONTHS)
    if young:
        score = min(5, score + 1)
    return score, f"{n} kênh nhỏ nổ, {young} kênh dưới {YOUNG_BOOM_MONTHS} tháng tuổi"


def _score_competition(competition: float) -> tuple[int, str]:
    c = competition
    score = 5 if c <= 10 else 4 if c <= 25 else 3 if c <= 40 else 2 if c <= 60 else 1
    return score, f"{c:.0f}% video top thuộc kênh > 1 triệu sub"


def _score_ad_price(rpm: list | tuple | None, tier: str | None) -> tuple[int | None, str]:
    if not rpm:
        return None, "chưa chọn nhóm chủ đề nên chưa có RPM"
    score = 5 if tier == RPM_HIGH else 3 if tier == RPM_MEDIUM else 2
    return score, f"RPM ≈ {rpm[0]:g}–{rpm[1]:g} USD (bậc {tier})"


def _score_money_other(affiliates: list[str]) -> tuple[int | None, str]:
    n = len(affiliates)
    if not n:
        return None, "chưa liệt kê sản phẩm affiliate"
    score = 5 if n >= 5 else 4 if n >= 3 else 3
    return score, f"{n} sản phẩm affiliate: {', '.join(affiliates[:5])}"


def auto_suggestions(
    runs: list[dict], keywords: dict[int, list[dict]] | None = None
) -> dict[int, dict[str, tuple[int | None, str]]]:
    """runs: các lần đào (dict từ db.runs, có "result", "affiliates", "category").
    keywords: {run_id: từ khoá dài đã chấm của ngách đó} (bài 2.3), nếu có.

    Trả về {run_id: {criterion: (điểm gợi ý hoặc None, căn cứ)}}. Ô thủ công có căn cứ là lời nhắc.
    """
    keywords = keywords or {}
    best_median = max((r["result"]["median_views"] or 0 for r in runs), default=0) or None
    out = {}
    for run in runs:
        r = run["result"]
        category = run.get("category") or ""
        policy_hint = (
            f"nhóm \"{category}\" nhạy cảm: cân nhắc chấm thấp" if category.startswith(SENSITIVE_CATEGORIES) else "chấm tay"
        )
        out[run["id"]] = {
            "demand": _score_demand(r["median_views"], best_median),
            "trend": _score_trend(r["trends"]),
            "small_channels": _score_small_channels(r["small_boom_channels"]),
            "competition": _score_competition(r["competition"]),
            "ad_price": _score_ad_price(r.get("rpm"), r.get("rpm_tier")),
            "money_other": _score_money_other(run.get("affiliates") or []),
            "production": (None, "chấm tay"),
            "policy_risk": (None, policy_hint),
            "passion": (None, "chấm tay"),
            "long_keywords": long_keyword_score(keywords.get(run["id"]) or []),
        }
    return out


def setup_warnings(runs: list[dict], max_days_apart: int = 3) -> list[str]:
    """Bài 2.4: chỉ so các ngách đào cùng thị trường, khoảng thời gian, định dạng và gần cùng ngày."""
    warnings = []
    for field, label in (("region", "thị trường"), ("window_days", "khoảng thời gian"), ("fmt", "định dạng")):
        values = {r[field] for r in runs}
        if len(values) > 1:
            warnings.append(f"Các ngách khác {label}: {', '.join(map(str, sorted(values)))}")
    dates = sorted(r["created_at"][:10] for r in runs)
    if dates:
        span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
        if span > max_days_apart:
            warnings.append(f"Các lần đào cách nhau {span} ngày (nên ≤ {max_days_apart} ngày); hãy đào lại cho cùng ngày")
    return warnings
