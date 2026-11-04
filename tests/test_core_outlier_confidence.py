import pytest

from tests.factories import channel, video
from yt_niche.core.confidence import HIGH, LOW, MEDIUM, confidence
from yt_niche.core.outlier import (
    LEVEL_OUTPERFORM,
    LEVEL_SUPER_VIRAL,
    LEVEL_VIRAL,
    channel_median_for,
    enrich_videos,
    outlier_level,
)


@pytest.mark.parametrize(
    "ratio,level",
    [(None, None), (2.99, None), (3, LEVEL_OUTPERFORM), (9.9, LEVEL_OUTPERFORM), (10, LEVEL_VIRAL), (50, LEVEL_SUPER_VIRAL)],
)
def test_outlier_level(ratio, level):
    assert outlier_level(ratio) == level


def test_channel_median_uses_matching_format_and_min_sample():
    ch = channel(median_long=1000, n_long=20, median_short=200, n_short=3)
    assert channel_median_for(ch, "long") == 1000
    assert channel_median_for(ch, "shorts") is None  # only 3 Shorts, fewer than 5
    assert channel_median_for(ch, "mid") is None


def test_enrich_marks_estimated():
    chans = {"A": channel("A", median_long=500), "H": channel("H", subs=None)}
    out = enrich_videos([video("1", "A", views=5000), video("2", "H", views=10), video("3", "X", views=10)], chans)
    assert out[0]["outlier"] == 10 and out[0]["level"] == LEVEL_VIRAL and not out[0]["estimated"]
    assert out[1]["estimated"]  # hidden subs
    assert out[2]["estimated"] and out[2]["outlier"] is None  # channel missing


@pytest.mark.parametrize(
    "n,est,expected",
    [(40, 12, HIGH), (40, 13, MEDIUM), (39, 0, MEDIUM), (14, 0, LOW), (15, 0, MEDIUM), (100, 61, LOW), (100, 60, MEDIUM)],
)
def test_confidence(n, est, expected):
    assert confidence(n, est) == expected
