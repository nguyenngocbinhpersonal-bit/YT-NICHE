"""The 10-test-video plan and the decision rules at video 5 and video 10 (lesson 2.5)."""

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
MIN_FOR_TREND = 3  # at least 3 measured videos before saying "rising"

KEEP, ADJUST, SWITCH = "KEEP", "ADJUST", "SWITCH"

# Signals to drop the niche even with views (the user ticks them).
DROP_SIGNALS = {
    "trend_and_booms_gone": "Google Trends 1-year line keeps falling and small booming channels vanished on re-dig",
    "demonetized": "Repeatedly limited ads or forced to work around copyright",
    "template_only": "Only works by repeating the exact same template",
    "no_money_path": "No path to money within 12 months and no product to recommend",
    "hate_it": "You hate making this niche",
}
DROP_FLAGS = DROP_SIGNALS  # glossary name

MEASURE_FIELDS = ("impressions", "ctr", "views", "avd_seconds", "retention", "new_subs")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def schedule(start: date, per_week: int, n: int = N_VIDEOS) -> list[date]:
    """Spread n videos evenly, per_week videos a week, starting at start."""
    return [start + timedelta(days=round(i * 7 / per_week)) for i in range(n)]


def default_pillar_order(pillars: list[str]) -> list[str]:
    """Rotate 3 pillars → 4 / 3 / 3 videos."""
    return [pillars[i % len(pillars)] for i in range(N_VIDEOS)]


def plan_issues(pillars: list[str], videos: list[dict]) -> list[str]:
    issues = []
    names = [p.strip() for p in pillars if p and p.strip()]
    if len(names) != N_PILLARS or len(set(names)) != N_PILLARS:
        issues.append(f"Needs exactly {N_PILLARS} different content pillars")
    counts = Counter(v.get("pillar") for v in videos)
    for p in names:
        if counts[p] < MIN_PER_PILLAR:
            issues.append(f"Pillar \"{p}\" has only {counts[p]} videos (needs at least {MIN_PER_PILLAR})")
    empty = [v["idx"] for v in videos if not (v.get("title") or "").strip()]
    if empty:
        issues.append(f"Videos without a title: {', '.join(map(str, empty))}")
    long = [v["idx"] for v in videos if len(v.get("title") or "") > TITLE_MAX]
    if long:
        issues.append(f"Title longer than {TITLE_MAX} characters: video {', '.join(map(str, long))}")
    return issues


# ---------------------------------------------------------------------------
# Reading the numbers after 7 days
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
            f"Low impressions (median {diag['median_impressions']:,.0f} < {cfg.TEST_IMPRESSIONS_LOW}): check that titles "
            "hold keywords people actually search for; put the main keyword at the start of the title."
        )
    if diag["ctr_low"]:
        actions.append(
            f"Low CTR (median {diag['median_ctr']:.1f}% < {cfg.TEST_CTR_LOW:g}%): fix the title and thumbnail; "
            "keep the niche if viewers still stay long."
        )
    if diag["retention_low"]:
        actions.append(
            f"Viewers leave early (retention {diag['median_retention']:.0f}% < {cfg.TEST_RETENTION_LOW:g}%): fix the hook and the first 30 seconds."
        )
    return actions


def review_at_5(videos: list[dict]) -> dict:
    diag = diagnose(videos)
    if diag["n"] < REVIEW_AT:
        return {"ready": False, "diag": diag, "actions": [], "message": f"Measured {diag['n']}/{REVIEW_AT} videos; the review point is not reached yet."}
    actions = fix_actions(diag) or ["No clear weakness yet: keep the posting rhythm, try title / thumbnail variants on the next video."]
    return {"ready": True, "diag": diag, "actions": actions, "message": "Video 5 checkpoint: fix titles, thumbnails and hooks before going on."}


def decide_at_10(videos: list[dict], *, fixes_applied: bool, out_of_ideas: bool) -> dict:
    diag = diagnose(videos)
    if out_of_ideas:
        return {"ready": True, "decision": SWITCH, "diag": diag, "reasons": ["Out of ideas before video 10"], "actions": ["Switch to the backup niche."]}
    if diag["n"] < DECIDE_AT:
        msg = f"Measured {diag['n']}/{DECIDE_AT} videos. Do not call the niche broken after only a few videos."
        return {"ready": False, "decision": None, "diag": diag, "reasons": [msg], "actions": []}

    keep_signals = []
    if diag["standouts"]:
        ids = ", ".join(str(v["idx"]) for v in diag["standouts"])
        keep_signals.append(f"{len(diag['standouts'])} videos clearly above the rest (video {ids})")
    if diag["avd_up"]:
        keep_signals.append("average view duration keeps rising")
    if diag["asks_more"]:
        keep_signals.append(f"{diag['asks_more']} videos have comments asking for more on this niche")

    if diag["standouts"] and len(keep_signals) >= 2:
        pillar = Counter(v.get("pillar") for v in diag["standouts"]).most_common(1)[0][0]
        return {
            "ready": True,
            "decision": KEEP,
            "diag": diag,
            "reasons": keep_signals,
            "actions": [f"Put more videos into pillar \"{pillar}\"."] if pillar else [],
        }

    no_movement = not diag["standouts"] and not diag["views_up"]
    if fixes_applied and (diag["impressions_low"] or no_movement):
        reasons = []
        if diag["impressions_low"]:
            reasons.append("fixes applied but impressions are still almost zero")
        if no_movement:
            reasons.append("no video moved")
        return {
            "ready": True,
            "decision": SWITCH,
            "diag": diag,
            "reasons": reasons,
            "actions": [
                "Before switching, separate a topic problem from an execution problem (title, thumbnail, hook, voice).",
                "Switch to the backup niche; keep the channel style if the backup serves a similar audience.",
            ],
        }

    actions = fix_actions(diag)
    if not fixes_applied:
        actions.append("Fixes at video 5 are not marked as applied: apply the suggestions and measure more before thinking about switching.")
    return {
        "ready": True,
        "decision": ADJUST,
        "diag": diag,
        "reasons": keep_signals or ["not enough signal to keep, but not enough ground to switch either"],
        "actions": actions or ["Try title / thumbnail / hook variants on the best pillar."],
    }


def auto_drop_hints(history: list[dict]) -> list[str]:
    """history: digs of the main niche with the same setup, newest first."""
    if not history:
        return []
    hints = []
    newest = history[0]["result"]
    if newest["trends"].get("class") == FALLING:
        hints.append("Latest dig: Google Trends are declining")
    if len(history) >= 2:
        before, now = len(history[1]["result"]["small_boom_channels"]), len(newest["small_boom_channels"])
        if before and now == 0:
            hints.append(f"Small booming channels vanished on re-dig ({before} → 0)")
    return hints
