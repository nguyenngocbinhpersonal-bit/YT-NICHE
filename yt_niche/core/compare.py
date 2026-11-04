"""Compare 2–5 niches in a 7-column table (lesson 2.4)."""

from __future__ import annotations

from itertools import combinations

from yt_niche import config as cfg
from yt_niche.core.confidence import HIGH, LOW, MEDIUM
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.reading import seven_cells
from yt_niche.core.scorecard import setup_warnings
from yt_niche.core.trends_classify import FAD, FALLING, SEASONAL, STABLE

# When scores are close, an evergreen niche beats a fad or a declining one.
TREND_RANK = {STABLE: 4, SEASONAL: 3, FAD: 1, FALLING: 0, None: 2}
ABILITY = {"long": "Long-form", "shorts": "Shorts", "both": "Both"}

COLUMNS = {
    "score": "Niche score",
    "confidence": "Confidence",
    "median_views": "Median views",
    "doors": "Outperformers / small booming",
    "trends": "Trends 1y / 5y",
    "income": "Income per video (≈)",
    "format": "Winning format",
}

GAP_CLEAR = "clear gap"
GAP_MODERATE = "moderate gap, check the other columns"
GAP_NOISE = "noise, treat as a tie"
GAP_TIE = "treat as a tie, decide by the other columns"


def gap_verdict(gap: float) -> str:
    gap = abs(gap)
    if gap >= cfg.COMPARE_CLEAR_GAP:
        return GAP_CLEAR
    if gap >= cfg.COMPARE_TIE_GAP:
        return GAP_MODERATE
    if gap <= cfg.COMPARE_NOISE_GAP:
        return GAP_NOISE
    return GAP_TIE


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
    """Best cell of each column (to highlight), among ranked niches only."""

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
    # Lesson 2.4: on a tie decide by Trends, small booming channels, money beyond ads (production is scored by hand in 2.5).
    return (TREND_RANK.get(r["trend_class"], 2), r["small_channels"], r["affiliates"], r["score"])


def _risk(r: dict) -> str:
    if r["trend_class"] == FALLING:
        return "Trends are declining: needs a fresh angle and a backup niche"
    if r["trend_class"] == FAD:
        return "Trends say fad: hard to build a whole channel"
    if r["small_channels"] < cfg.SUPPLY_WEAK_SMALL_CHANNELS:
        return f"only {r['small_channels']} small booming channels: may be a big-channel niche"
    if r["confidence"] != HIGH:
        return f"confidence {r['confidence']} ({r['n']} videos): re-dig for a bigger sample"
    if not r["income"]:
        return "no RPM yet, so the Money tier is unknown"
    if r["trend_class"] is None:
        return "no Google Trends yet to check for evergreen demand"
    return "estimated income is only for relative comparison, not a salary figure"


def compare(runs: list[dict], ability: str | None = None) -> dict:
    """runs: dig runs (dicts from db.runs). ability: long | shorts | both — the format you can produce."""
    all_rows = [_row(r, ability) for r in runs]
    excluded = [r for r in all_rows if r["confidence"] == LOW]
    rows = sorted((r for r in all_rows if r["confidence"] != LOW), key=lambda r: -r["score"])

    warnings = setup_warnings(runs)
    for run in runs:
        sheet = seven_cells(run)
        if not sheet["complete"]:
            warnings.append(f"{run['topic']}: 7-cell sheet incomplete (missing {', '.join(sheet['missing'])})")
    ns = [r["n"] for r in rows if r["n"]]
    if ns and max(ns) / min(ns) >= cfg.COMPARE_SAMPLE_RATIO:
        warnings.append(f"Sample sizes differ a lot ({min(ns)} and {max(ns)} videos): absolute numbers are not comparable")

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
            why = f"highest score ({top['score']:.0f})" + (
                f", {top['score'] - second['score']:.0f} points ahead of {second['name']} ({gap_verdict(top['score'] - second['score'])})"
                if second
                else ""
            )
        else:
            leader = max(close, key=_tie_key)
            names = ", ".join(f"{r['name']} {r['score']:.0f}" for r in close)
            why = f"tied ({names}), decided by Trends → small booming channels → affiliates"

    big_channel_niches = [
        r["name"] for r in rows if r["score"] >= cfg.LABEL_POTENTIAL_MIN and r["small_channels"] < cfg.SUPPLY_WEAK_SMALL_CHANNELS
    ]
    # "Doors" of the losing niches: while small channels still boom, do not drop them without opening the boom cards.
    doors = [
        f"{r['name']}: still {r['small_channels']} small booming channels" + (f", fits {ABILITY.get(r['format_fit'], r['format_fit'])}" if r["format_fit"] else "")
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
    """The column where the leader gains most (against the best runner-up in each column)."""
    others = [r for r in rows if r["id"] != leader["id"]]
    if not others:
        return "only one niche is confident enough to rank"
    gains = []
    best_other_median = max((r["median_views"] or 0) for r in others)
    if leader["median_views"] and best_other_median:
        gains.append((leader["median_views"] / best_other_median, f"median views {leader['median_views'] / best_other_median:.1f}× higher"))
    best_other_small = max(r["small_channels"] for r in others)
    if leader["small_channels"] > best_other_small:
        gains.append(((leader["small_channels"] + 1) / (best_other_small + 1), f"{leader['small_channels']} small booming channels vs at most {best_other_small}"))
    if TREND_RANK.get(leader["trend_class"], 2) > max(TREND_RANK.get(r["trend_class"], 2) for r in others):
        gains.append((1.5, f"Trends {leader['trend_class']} beats the other niches"))
    best_other_income = max((r["income"][1] for r in others if r["income"]), default=0)
    if leader["income"] and best_other_income:
        gains.append((leader["income"][1] / best_other_income, f"income per video ≈ {leader['income'][1] / best_other_income:.1f}× higher"))
    gains = [g for g in gains if g[0] > 1]
    if not gains:
        return "no column stands out clearly; rely on the total score and the hand-scored criteria in lesson 2.5"
    return max(gains)[1]
