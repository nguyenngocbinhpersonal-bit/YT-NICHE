"""Chạy engine trên dữ liệu thật đã lưu: 'kể chuyện lịch sử', VN, 30 ngày, mọi độ dài (17/10/2026)."""

import json
from pathlib import Path

import pytest

from yt_niche.core.analyze import analyze
from yt_niche.core.confidence import HIGH

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture(scope="module")
def result():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return analyze(data, rpm=(0.5, 1.0), affiliates=3)


def test_sample_and_confidence(result):
    assert result["n"] == 98  # 59 dài + 39 Shorts, không có video 3–4 phút
    assert result["confidence"] == HIGH and result["can_shortlist"]


def test_score_is_consistent(result):
    expected = (
        0.35 * result["demand"]
        + 0.25 * result["opportunity"]
        + 0.20 * result["freshness"]
        + 0.20 * (100 - result["competition"])
    )
    assert result["score"] == pytest.approx(expected, abs=0.1)
    assert result["labels"][0] == "Ngách xanh"  # DeepNiche cũng chấm ngách này là xanh (75/100)


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
    assert t["class"] == "theo mùa" and t["peak_month"] in (2, 3) and t["direction"] == "đi lên"


def test_note_and_verdict(result):
    assert result["three_tier"]["verdict"] in ("THỬ", "THỬ SAU KHI SỬA", "BỎ")
    assert result["note"].startswith("kể chuyện lịch sử — ")
