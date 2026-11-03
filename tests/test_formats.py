from yt_niche.core.formats import LONG, MID, SHORTS, classify, parse_duration


def test_parse_duration():
    assert parse_duration("PT45S") == 45
    assert parse_duration("PT3M") == 180
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration("P1DT1S") == 86401
    assert parse_duration("P0D") == 0
    assert parse_duration("") == 0


def test_classify_boundaries():
    assert classify(180) == SHORTS
    assert classify(181) == MID
    assert classify(239) == MID
    assert classify(240) == LONG
