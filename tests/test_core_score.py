import pytest

from tests.factories import NOW, channel, enriched
from yt_niche.core.format_split import format_split
from yt_niche.core.money import RPM_HIGH, RPM_LOW, RPM_MEDIUM, income_per_video, rpm_tier
from yt_niche.core.niche_score import (
    LABEL_GREEN,
    LABEL_POTENTIAL,
    LABEL_RED,
    competition_bar,
    demand_bar,
    freshness_bar,
    niche_label,
    niche_score,
    opportunity_bar,
    small_boom_channels,
)


@pytest.mark.parametrize("views,expected", [(1000, 0), (1_000_000, 100), (31_623, 50), (100, 0), (10**8, 100)])
def test_demand_log_scale(views, expected):
    assert demand_bar([enriched(views=views)])[0] == pytest.approx(expected, abs=0.1)


def test_opportunity_is_share_of_outliers_from_small_channels():
    sample = [
        enriched(subs=5000, outlier=5, level="x"),
        enriched(subs=500_000, outlier=5, level="x"),
        enriched(subs=5000),  # không vượt trội: không tính
    ]
    assert opportunity_bar(sample) == 50
    assert opportunity_bar([enriched()]) == 0


def test_competition_counts_channels_over_1m():
    sample = [enriched(subs=2_000_000), enriched(subs=1_000_000), enriched(subs=None), enriched(subs=10)]
    assert competition_bar(sample) == 25


def test_freshness_share_within_30_days_for_long_window():
    sample = [enriched(days_ago=10), enriched(days_ago=40), enriched(days_ago=29), enriched(days_ago=200)]
    assert freshness_bar(sample, 90, NOW) == 50


def test_freshness_views_per_hour_for_30_day_window():
    # 1.000 giờ tuổi: 1.000 view = 1 view/giờ → 0 điểm; 1.000.000 view = 1.000 view/giờ → 100 điểm
    hours = 1000 / 24
    assert freshness_bar([enriched(views=1000, days_ago=hours)], 30, NOW) == pytest.approx(0, abs=0.1)
    assert freshness_bar([enriched(views=1_000_000, days_ago=hours)], 30, NOW) == pytest.approx(100, abs=0.1)


@pytest.mark.parametrize("score,label", [(70, LABEL_GREEN), (69.9, LABEL_POTENTIAL), (50, LABEL_POTENTIAL), (49.9, LABEL_RED)])
def test_label_thresholds(score, label):
    assert niche_label(score) == label


def test_niche_score_weights():
    # Nhu cầu 100, Cơ hội 100, Độ mới 100 (đăng trong 30 ngày), Cạnh tranh 0 → 100
    sample = [enriched(views=1_000_000, subs=5000, outlier=5, level="x", days_ago=1)]
    s = niche_score(sample, 90, NOW)
    assert (s.demand, s.opportunity, s.freshness, s.competition, s.score) == (100, 100, 100, 0, 100)


def test_small_boom_channels_best_video_and_young_first():
    chans = {"OLD": channel("OLD", created="2020-01-01T00:00:00Z"), "NEW": channel("NEW", created="2026-08-01T00:00:00Z")}
    sample = [
        enriched(channel="OLD", outlier=40, level="x"),
        enriched(channel="NEW", outlier=4, level="x"),
        enriched(channel="NEW", outlier=8, level="x"),
        enriched(channel="BIG", subs=200_000, outlier=99, level="x"),
    ]
    cards = small_boom_channels(sample, chans, NOW)
    assert [c["channel_id"] for c in cards] == ["NEW", "OLD"]
    assert cards[0]["best_outlier"] == 8 and cards[0]["age_months"] == 2 and cards[0]["young"]


def _many(n, views, fmt, outliers=0):
    return [
        enriched(views=views, fmt=fmt, outlier=5 if i < outliers else None, level="x" if i < outliers else None)
        for i in range(n)
    ]


def test_format_split_labels():
    assert format_split(_many(10, 3000, "long", 4) + _many(10, 1000, "shorts", 2))["fit"] == "long"
    assert format_split(_many(10, 1000, "long", 1) + _many(10, 5000, "shorts", 3))["fit"] == "shorts"
    # Shorts thắng trung vị nhưng thua số vượt trội → không gắn nhãn
    assert format_split(_many(10, 1000, "long", 5) + _many(10, 5000, "shorts", 1))["fit"] is None
    # Thiếu mẫu
    assert format_split(_many(10, 3000, "long", 4) + _many(4, 1000, "shorts"))["fit"] is None


def test_money():
    assert income_per_video(20_000, 0.5, 1.0) == (10.0, 20.0)
    assert income_per_video(None, 1, 2) is None
    assert rpm_tier(0.3, 0.8) == RPM_LOW
    assert rpm_tier(1, 3) == RPM_MEDIUM
    assert rpm_tier(4, 8) == RPM_HIGH
