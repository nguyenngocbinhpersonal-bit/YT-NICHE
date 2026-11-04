"""Run the engine on saved real data: 'kể chuyện lịch sử' (Vietnamese history stories), VN, 30 days, any length (17/10/2026)."""

import json
from pathlib import Path

import pytest

from yt_niche.core.analyze import analyze
from yt_niche.core.confidence import HIGH
from yt_niche.core.niche_score import LABEL_GREEN
from yt_niche.core.three_tier import DROP, TRY, TRY_AFTER_FIX
from yt_niche.core.trends_classify import SEASONAL, UP

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture(scope="module")
def result():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return analyze(data, rpm=(0.5, 1.0), affiliates=3)


def test_sample_and_confidence(result):
    assert result["n"] == 98  # 59 long-form + 39 Shorts, no 3–4 minute videos
    assert result["confidence"] == HIGH and result["can_shortlist"]


def test_score_is_consistent(result):
    expected = (
        0.35 * result["demand"]
        + 0.25 * result["opportunity"]
        + 0.20 * result["freshness"]
        + 0.20 * (100 - result["competition"])
    )
    assert result["score"] == pytest.approx(expected, abs=0.1)
    assert result["labels"][0] == LABEL_GREEN  # DeepNiche also rates this niche green (75/100)


def test_outliers_use_same_format_median(result):
    top = result["outliers"][0]
    assert top["fmt"] == "shorts"
    assert top["outlier"] == pytest.approx(top["views"] / top["channel_median"])
    assert all(v["outlier"] >= 3 for v in result["outliers"])
    assert result["outliers"] == sorted(result["outliers"], key=lambda v: v["outlier"], reverse=True)


def test_supply_signals(result):
    booms = result["small_boom_channels"]
    assert len(booms) >= 5 and not result["suggest_sub_niche"]
    assert all(c["subs"] < 100_000 and c["best_outlier"] >= 3 for c in booms)


def test_trends_seasonal_tet_peak(result):
    t = result["trends"]
    assert t["class"] == SEASONAL and t["peak_month"] in (2, 3) and t["direction"] == UP


def test_note_and_verdict(result):
    assert result["three_tier"]["verdict"] in (TRY, TRY_AFTER_FIX, DROP)
    assert result["note"].startswith("kể chuyện lịch sử — ")
