"""So 2–5 ngách bằng bảng 7 cột (bài 2.4)."""

from __future__ import annotations

from itertools import combinations

from yt_niche import config as cfg
from yt_niche.core.confidence import HIGH, LOW, MEDIUM
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.reading import seven_cells
from yt_niche.core.scorecard import setup_warnings
from yt_niche.core.trends_classify import FAD, FALLING, SEASONAL, STABLE

# Khi điểm gần nhau, ngách bền thắng ngách trào lưu hoặc đang giảm.
TREND_RANK = {STABLE: 4, SEASONAL: 3, FAD: 1, FALLING: 0, None: 2}
ABILITY = {"long": "Video dài", "shorts": "Shorts", "both": "Cả hai"}

COLUMNS = {
    "score": "Điểm ngách",
    "confidence": "Độ tin cậy",
    "median_views": "Lượt xem trung vị",
    "doors": "Vượt trội / kênh nhỏ nổ",
    "trends": "Trends 1 năm / 5 năm",
    "income": "Thu nhập/video (≈)",
    "format": "Định dạng thắng",
}


def gap_verdict(gap: float) -> str:
    gap = abs(gap)
    if gap >= cfg.COMPARE_CLEAR_GAP:
        return "khác biệt rõ"
    if gap >= cfg.COMPARE_TIE_GAP:
        return "chênh vừa, xem thêm các cột khác"
    if gap <= cfg.COMPARE_NOISE_GAP:
        return "nhiễu, coi như ngang"
    return "coi như ngang, quyết bằng cột khác"


def _format_fit(run: dict, ability: str | None) -> tuple[str | None, bool]:
    fs = run["result"].get("format_split")
    fit = fs["fit"] if fs else run["result"]["params"]["fmt"] if run["result"]["params"]["fmt"] in (LONG, SHORTS) else None
    match = bool(ability) and (ability == "both" or fit is None or fit == ability)
    return fit, match


def _row(run: dict, ability: str | None) -> dict:
    r = run["result"]
    fit, match = _format_fit(run, ability)
    income = r.get("income_per_video")
    return {
        "id": run["id"],
        "name": run["topic"],
        "score": r["score"],
        "label": r["labels"][0],
        "confidence": r["confidence"],
        "n": r["n"],
        "median_views": r["median_views"],
        "outliers": r["outlier_counts"]["total"],
        "small_channels": len(r["small_boom_channels"]),
        "trend_class": r["trends"].get("class"),
        "trend_12m": r["trends"].get("trend_12m"),
        "trend_5y": r["trends"].get("trend_5y"),
        "income": income,
        "affiliates": len(run.get("affiliates") or []),
        "format_fit": fit,
        "format_match": match,
    }


def _best(rows: list[dict]) -> dict[str, set[int]]:
    """Ô tốt nhất mỗi cột (để tô xanh), chỉ tính trong các ngách được xếp hạng."""

    def top(key) -> set[int]:
        vals = [(key(r), r["id"]) for r in rows if key(r) is not None]
        if not vals:
            return set()
        m = max(v for v, _ in vals)
        return {i for v, i in vals if v == m}

    conf_rank = {HIGH: 2, MEDIUM: 1}
    return {
        "score": top(lambda r: r["score"]),
        "confidence": top(lambda r: conf_rank.get(r["confidence"], 0)),
        "median_views": top(lambda r: r["median_views"]),
        "doors": top(lambda r: (r["small_channels"], r["outliers"])),
        "trends": top(lambda r: TREND_RANK.get(r["trend_class"], 2)),
        "income": top(lambda r: r["income"][1] if r["income"] else None),
        "format": {r["id"] for r in rows if r["format_match"]},
    }


def _tie_key(r: dict) -> tuple:
    # Bài 2.4: ngang điểm thì quyết bằng Trends, kênh nhỏ nổ, nguồn thu ngoài quảng cáo (sản xuất: chấm tay ở bài 2.5).
    return (TREND_RANK.get(r["trend_class"], 2), r["small_channels"], r["affiliates"], r["score"])


def _risk(r: dict) -> str:
    if r["trend_class"] == FALLING:
        return "Trends đang rơi: cần góc mới và ngách dự phòng"
    if r["trend_class"] == FAD:
        return "Trends là trào lưu: khó xây cả kênh"
    if r["small_channels"] < cfg.SUPPLY_WEAK_SMALL_CHANNELS:
        return f"chỉ {r['small_channels']} kênh nhỏ nổ: có thể là ngách của kênh lớn"
    if r["confidence"] != HIGH:
        return f"độ tin cậy {r['confidence']} ({r['n']} video): nên đào lại cho đủ mẫu"
    if not r["income"]:
        return "chưa có RPM nên chưa biết tầng Tiền"
    if r["trend_class"] is None:
        return "chưa có Google Trends để kiểm tra cầu bền"
    return "thu nhập ước tính chỉ để so tương đối, không phải số để tính lương"


def compare(runs: list[dict], ability: str | None = None) -> dict:
    """runs: lần đào (dict từ db.runs). ability: long | shorts | both — định dạng bạn làm được."""
    all_rows = [_row(r, ability) for r in runs]
    excluded = [r for r in all_rows if r["confidence"] == LOW]
    rows = sorted((r for r in all_rows if r["confidence"] != LOW), key=lambda r: -r["score"])

    warnings = setup_warnings(runs)
    for run in runs:
        sheet = seven_cells(run)
        if not sheet["complete"]:
            warnings.append(f"{run['topic']}: phiếu 7 ô chưa đủ (thiếu {', '.join(sheet['missing'])})")
    ns = [r["n"] for r in rows if r["n"]]
    if ns and max(ns) / min(ns) >= cfg.COMPARE_SAMPLE_RATIO:
        warnings.append(f"Cỡ mẫu chênh nhiều ({min(ns)} và {max(ns)} video): không so số tuyệt đối ngang nhau được")

    medians = [r["median_views"] for r in rows if r["median_views"]]
    view_ratio = round(max(medians) / min(medians), 1) if len(medians) >= 2 else None

    pairs = [
        {"a": a["name"], "b": b["name"], "gap": round(a["score"] - b["score"], 1), "verdict": gap_verdict(a["score"] - b["score"])}
        for a, b in combinations(rows, 2)
    ]

    leader, why = None, ""
    if rows:
        top = rows[0]
        close = [r for r in rows if top["score"] - r["score"] < cfg.COMPARE_TIE_GAP]
        if len(close) == 1:
            leader = top
            second = rows[1] if len(rows) > 1 else None
            why = f"điểm cao nhất ({top['score']:.0f})" + (
                f", hơn {second['name']} {top['score'] - second['score']:.0f} điểm ({gap_verdict(top['score'] - second['score'])})"
                if second
                else ""
            )
        else:
            leader = max(close, key=_tie_key)
            names = ", ".join(f"{r['name']} {r['score']:.0f}" for r in close)
            why = f"ngang điểm ({names}), quyết bằng Trends → kênh nhỏ nổ → affiliate"

    big_channel_niches = [
        r["name"] for r in rows if r["score"] >= cfg.LABEL_POTENTIAL_MIN and r["small_channels"] < cfg.SUPPLY_WEAK_SMALL_CHANNELS
    ]
    # "Ngõ" của ngách thua: còn kênh nhỏ nổ thì đừng loại mà không mở khung kênh nổ.
    doors = [
        f"{r['name']}: còn {r['small_channels']} kênh nhỏ nổ" + (f", hợp {ABILITY.get(r['format_fit'], r['format_fit'])}" if r["format_fit"] else "")
        for r in rows
        if leader and r["id"] != leader["id"] and r["small_channels"] > 0
    ]

    return {
        "rows": rows,
        "excluded": excluded,
        "best": _best(rows),
        "pairs": pairs,
        "view_ratio": view_ratio,
        "warnings": warnings,
        "leader": leader,
        "leader_reason": why,
        "differentiator": _differentiator(leader, rows) if leader else None,
        "risk": _risk(leader) if leader else None,
        "big_channel_niches": big_channel_niches,
        "doors": doors,
    }


def _differentiator(leader: dict, rows: list[dict]) -> str:
    """Cột tạo khác biệt lớn nhất cho ngách dẫn đầu (so với ngách đứng sau tốt nhất ở từng cột)."""
    others = [r for r in rows if r["id"] != leader["id"]]
    if not others:
        return "chỉ có một ngách đủ tin cậy để xếp hạng"
    gains = []
    best_other_median = max((r["median_views"] or 0) for r in others)
    if leader["median_views"] and best_other_median:
        gains.append((leader["median_views"] / best_other_median, f"lượt xem trung vị gấp {leader['median_views'] / best_other_median:.1f} lần"))
    best_other_small = max(r["small_channels"] for r in others)
    if leader["small_channels"] > best_other_small:
        gains.append(((leader["small_channels"] + 1) / (best_other_small + 1), f"{leader['small_channels']} kênh nhỏ nổ so với tối đa {best_other_small}"))
    if TREND_RANK.get(leader["trend_class"], 2) > max(TREND_RANK.get(r["trend_class"], 2) for r in others):
        gains.append((1.5, f"Trends {leader['trend_class']} tốt hơn các ngách còn lại"))
    best_other_income = max((r["income"][1] for r in others if r["income"]), default=0)
    if leader["income"] and best_other_income:
        gains.append((leader["income"][1] / best_other_income, f"thu nhập/video ≈ gấp {leader['income'][1] / best_other_income:.1f} lần"))
    gains = [g for g in gains if g[0] > 1]
    if not gains:
        return "không cột nào vượt rõ; dựa vào điểm tổng và các tiêu chí chấm tay ở bài 2.5"
    return max(gains)[1]
