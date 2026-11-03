"""Gộp toàn bộ phép tính của một lần đào từ dữ liệu thô của services.fetch."""

from __future__ import annotations

from dataclasses import asdict

from yt_niche.core.confidence import LOW, confidence
from yt_niche.core.format_split import format_split
from yt_niche.core.formats import LONG, MID, SHORTS
from yt_niche.core.money import income_per_video, rpm_tier
from yt_niche.core.niche_score import niche_score, small_boom_channels
from yt_niche.core.outlier import enrich_videos
from yt_niche.core.stats import parse_iso
from yt_niche.core.three_tier import three_tier
from yt_niche.core.trends_classify import classify_trends
from yt_niche import config as cfg

FEW_SAMPLES = "Mẫu còn ít"


def analyze(
    data: dict,
    *,
    rpm: tuple[float, float] | None = None,
    affiliates: int | None = None,
) -> dict:
    """data: kết quả fetch_niche. rpm: (thấp, cao) USD/1.000 view. affiliates: số sản phẩm gắn tự nhiên."""
    params = data["params"]
    now = parse_iso(data["fetched_at"])
    channels = {c["channel_id"]: c for c in data["channels"]}

    videos = enrich_videos(data["videos"], channels)
    target = params["fmt"]
    # "Mọi độ dài": bỏ vùng 3–4 phút vì không thuộc định dạng nào.
    sample = [v for v in videos if v["fmt"] == target] if target in (LONG, SHORTS) else [v for v in videos if v["fmt"] != MID]

    score = niche_score(sample, params["window_days"], now)
    n_estimated = sum(v["estimated"] for v in sample)
    conf = confidence(len(sample), n_estimated)

    outliers = sorted((v for v in sample if v["level"]), key=lambda v: v["outlier"], reverse=True)
    booms = small_boom_channels(sample, channels, now)
    trends = classify_trends((data.get("trends") or {}).get("series"))

    income = income_per_video(score.median_views, *rpm) if rpm else None
    tier_rpm = rpm_tier(*rpm) if rpm else None
    tiers = three_tier(
        demand_bar=score.demand,
        trend_class=trends["class"],
        outliers=len(outliers),
        small_channels=len(booms),
        competition=score.competition,
        rpm_tier=tier_rpm,
        affiliates=affiliates,
    )

    labels = [score.label]
    if conf == LOW:
        labels.append(FEW_SAMPLES)

    return {
        "params": params,
        "fetched_at": data["fetched_at"],
        **asdict(score),
        "labels": labels,
        "confidence": conf,
        "n_estimated": n_estimated,
        "can_shortlist": conf != LOW,  # độ tin cậy thấp thì không đưa vào bảng chấm
        "outliers": outliers,
        "outlier_counts": {
            "total": len(outliers),
            "viral": sum(v["outlier"] >= cfg.OUTLIER_VIRAL for v in outliers),
            "super_viral": sum(v["outlier"] >= cfg.OUTLIER_SUPER_VIRAL for v in outliers),
        },
        "small_boom_channels": booms,
        "suggest_sub_niche": len(booms) < cfg.SUPPLY_WEAK_SMALL_CHANNELS,
        "format_split": format_split(videos) if target == "any" else None,
        "trends": trends,
        "rpm": rpm,
        "rpm_tier": tier_rpm,
        "income_per_video": income,
        "three_tier": tiers,
        "note": _note(params["topic"], score.score, len(outliers), len(booms), income, tiers["verdict"]),
    }


def _note(topic: str, score: float, outliers: int, booms: int, income, verdict: str) -> str:
    money = f"{income[0]:g}–{income[1]:g} USD/video (≈)" if income else "chưa có RPM"
    return f"{topic} — {score:.0f}/100 — {outliers} vượt trội / {booms} kênh nhỏ nổ — {money} — {verdict}"
