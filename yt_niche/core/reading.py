"""Read the results of one dig (lessons 2.1, 2.2): 7-cell sheet, advice per score zone, confidence, format, monetization."""

from __future__ import annotations

from yt_niche import config as cfg
from yt_niche.core.confidence import HIGH, LOW, MEDIUM
from yt_niche.core.formats import LONG, SHORTS
from yt_niche.core.text import no_space, word_count

# YouTube ad monetization requirements (lesson 2.1).
MONETIZATION = (
    "For ads: 1,000 subscribers plus 4,000 watch hours of long-form video in 12 months, "
    "or 1,000 subscribers plus 10 million Shorts views in 90 days. Affiliate income needs no monetization."
)

FORMAT_LABEL = {LONG: "long-form", SHORTS: "Shorts"}


def zone_advice(score: float) -> str:
    """Three score zones and what to do (lesson 2.2)."""
    if score >= cfg.LABEL_GREEN_MIN:
        return "Worth entering; favour sub-niches and long-tail keywords over a generic channel."
    if score >= cfg.LABEL_POTENTIAL_MIN:
        return "Needs a distinct angle (audience, market, format). Dig 2–3 sub-niches before deciding."
    return "Avoid it, or dig a narrower sub-niche, or change market."


def confidence_advice(confidence: str) -> str:
    if confidence == LOW:
        return (
            "Small sample: no conclusion is possible, whatever the score. Widen the time window (e.g. 30 → 90 days), "
            "use 2–4 more specific words or change the keyword, then dig again. Do not lock in the niche."
        )
    if confidence == MEDIUM:
        return "Medium confidence: fine for comparing, but open a few real channels before locking in."
    return "High confidence: enough sample to compare and lock in."


ESTIMATE_NOTE = (
    "Numbers marked ≈ are estimates (hidden subs, hidden views, channels with too few videos for a median): "
    "use them for direction only, not to compare differences of a few percent."
)


def format_advice(fit: str | None) -> str | None:
    if fit == LONG:
        return "Long-form has more outperformer videos: make long-form the core, use Shorts as bait."
    if fit == SHORTS:
        return (
            "Shorts dominate, but Shorts earn far less per view: plan the money around affiliates "
            "or the 10 million Shorts views in 90 days milestone; do not conclude Shorts earn more just because views are high."
        )
    return None


def topic_warning(topic: str, language: str) -> str | None:
    """A topic should be 2–4 specific words (Vietnamese counts syllables, so up to 8 is allowed)."""
    if no_space(language):
        return None
    n = word_count(topic, language)
    hi = 8 if language == "vi" else 4
    if n < 2:
        return "A one-word topic is usually too broad: use 2–4 specific words."
    if n > hi:
        return "A very long topic tends to be a video topic, not a niche: use 2–4 specific words."
    return None


def seven_cells(run: dict) -> dict:
    """7-cell sheet (lesson 2.2). Only a complete sheet with medium or high confidence goes into the comparison in lesson 2.4."""
    r = run["result"]
    params = r["params"]
    fs = r.get("format_split")
    if params["fmt"] in (LONG, SHORTS):
        fmt_value = f"dug as {FORMAT_LABEL[params['fmt']]} only"
    elif fs:
        fmt_value = fs.get("fit_label") or "no format wins clearly"
    else:
        fmt_value = None
    trend = r["trends"].get("class")
    cells = [
        ("score", "Score + label", f"{r['score']:.0f} · {r['labels'][0]}", True),
        ("confidence", "Confidence", f"{r['confidence']} ({r['n']} videos)", r["confidence"] in (HIGH, MEDIUM)),
        (
            "bars",
            "Four bars",
            f"Demand {r['demand']:.0f} · Opportunity {r['opportunity']:.0f} · Freshness {r['freshness']:.0f} · Competition {r['competition']:.0f}",
            True,
        ),
        ("outliers", "Outperformer videos", str(r["outlier_counts"]["total"]), True),
        ("small_channels", "Small booming channels", str(len(r["small_boom_channels"])), True),
        ("format", "Winning format", fmt_value or "none yet", fmt_value is not None),
        ("trends", "Trends shape", trend or "no Trends yet", trend is not None),
    ]
    rows = [{"key": k, "label": label, "value": v, "ok": ok} for k, label, v, ok in cells]
    missing = [c["label"] for c in rows if not c["ok"]]
    return {"cells": rows, "complete": not missing, "missing": missing}
