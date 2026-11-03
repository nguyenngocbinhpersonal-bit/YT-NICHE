import math

import pytest

from tests.factories import weekly
from yt_niche.core.three_tier import DROP, MEDIUM, STRONG, TRY, TRY_AFTER_FIX, WEAK, rate_money, three_tier
from yt_niche.core.trends_classify import FAD, FALLING, SEASONAL, STABLE, classify_trends

N = 261  # số tuần trong 5 năm


def test_stable_flat():
    r = classify_trends({"5y": weekly([50 + (i % 3) for i in range(N)])})
    assert r["class"] == STABLE and r["direction"] == "đi ngang"


def test_stable_rising():
    r = classify_trends({"5y": weekly([20 + i * 0.3 for i in range(N)])})
    assert r["class"] == STABLE and r["direction"] == "đi lên"


def test_fad():
    values = [2] * (N - 13) + [60] * 13
    assert classify_trends({"5y": weekly(values)})["class"] == FAD


def test_low_but_not_spiking_is_not_fad():
    values = [3] * (N - 13) + [12] * 13  # gấp 4 lần nền nhưng vẫn thấp (< 20)
    assert classify_trends({"5y": weekly(values)})["class"] != FAD


def test_falling():
    values = [100 - i * 0.3 for i in range(N)]
    r = classify_trends({"5y": weekly(values), "12m": weekly(values[-53:])})
    assert r["class"] == FALLING


def test_seasonal_even_with_rising_base():
    values = [20 + i * 0.1 + 30 * max(0, math.cos(2 * math.pi * i / 52)) ** 4 for i in range(N)]
    r = classify_trends({"5y": weekly(values)})
    assert r["class"] == SEASONAL and r["direction"] == "đi lên" and "peak_month" in r


def test_missing_trends():
    assert classify_trends(None)["class"] is None
    assert classify_trends({"5y": weekly([1] * 50)})["class"] is None


def test_sparse_flag():
    assert classify_trends({"5y": weekly([0, 0, 5] * 87)})["sparse"]


def _tiers(**kw):
    base = dict(
        demand_bar=70, trend_class=STABLE, outliers=12, small_channels=6, competition=10, rpm_tier="vừa", affiliates=3
    )
    return three_tier(**{**base, **kw})


def test_all_strong_is_try():
    r = _tiers()
    assert r["demand"]["level"] == STRONG and r["supply"]["level"] == STRONG and r["verdict"] == TRY


@pytest.mark.parametrize(
    "kw,tier",
    [
        ({"demand_bar": 29}, "demand"),
        ({"trend_class": FALLING}, "demand"),
        ({"small_channels": 2}, "supply"),
        ({"competition": 61}, "supply"),
        ({"rpm_tier": "thấp", "affiliates": 0}, "money"),
    ],
)
def test_one_weak_tier_needs_fix(kw, tier):
    r = _tiers(**kw)
    assert r[tier]["level"] == WEAK and r["verdict"] == TRY_AFTER_FIX and len(r["fixes"]) == 1


def test_two_weak_tiers_drop():
    assert _tiers(demand_bar=10, small_channels=0)["verdict"] == DROP


def test_no_trends_caps_demand_at_medium():
    assert _tiers(trend_class=None)["demand"]["level"] == MEDIUM


def test_seasonal_can_be_strong_demand():
    assert _tiers(trend_class=SEASONAL)["demand"]["level"] == STRONG


def test_money_combinations():
    assert rate_money(None, None)[0] == MEDIUM
    assert rate_money("thấp", 3)[0] == MEDIUM  # (1 + 3) // 2 = 2
    assert rate_money("cao", None)[0] == STRONG
    assert rate_money(None, 0)[0] == WEAK
