"""Google Trends blocked with 429: stop early, cool down, cache per range, derive 12 months, refetch for a saved dig."""

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest

from yt_niche import config as cfg
from yt_niche.db import runs as run_repo
from yt_niche.services import runs as run_svc
from yt_niche.sources import trends

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


class TooManyRequestsError(Exception):
    pass


class FakeClient:
    """Returns 429 for the ranges in `blocked`, weekly series otherwise."""

    calls: list[str] = []
    blocked: set[str] = set()
    values = staticmethod(lambda i, n: i % 50 + 1)

    def __init__(self, *a, **k):
        pass

    def build_payload(self, topics, timeframe, geo, gprop):
        FakeClient.calls.append(timeframe)
        self.topics, self.timeframe = topics, timeframe
        if timeframe in FakeClient.blocked:
            raise TooManyRequestsError("Google returned a response with code 429")

    def interest_over_time(self):
        n = {"today 5-y": 261, "today 12-m": 53, "today 3-m": 93}[self.timeframe]
        idx = pd.date_range(end="2026-10-11", periods=n, freq="W" if n != 93 else "D")
        return pd.DataFrame({t: [FakeClient.values(i, n) for i in range(n)] for t in self.topics}, index=idx)


@pytest.fixture
def fake(monkeypatch):
    FakeClient.calls, FakeClient.blocked = [], set()
    FakeClient.values = staticmethod(lambda i, n: i % 50 + 1)
    monkeypatch.setattr(trends, "TrendReq", FakeClient)
    monkeypatch.setattr(trends, "RETRY_WAITS", (0, 0))
    monkeypatch.setattr(trends, "PAUSE_SECONDS", 0)
    return FakeClient


def test_stops_on_429_and_cools_down(conn, fake):
    fake.blocked = {"today 5-y"}
    r = asyncio.run(trends.get_trends(conn, "story", "US"))
    assert fake.calls == ["today 5-y"] * 3  # 1 call + 2 retries, no further 12m/3m calls
    assert set(r["errors"]) == {"5y", "12m", "3m"} and r["series"] == {}
    assert trends.cooldown_until(conn) is not None
    assert "clears on its own" in trends.describe_errors(r["errors"])

    fake.calls.clear()
    fake.blocked = set()
    r = asyncio.run(trends.get_trends(conn, "story", "US"))  # cooling down: Google is not called
    assert fake.calls == [] and trends.PAUSED in r["errors"]["5y"]


def test_one_call_derives_12m_and_3m(conn, fake):
    r = asyncio.run(trends.get_trends(conn, "story", "US"))
    assert fake.calls == ["today 5-y"]  # 1 call instead of 3
    assert len(r["series"]["5y"]) == 261 and r["derived"] == ["12m", "3m"] and r["errors"] == {}
    assert 50 <= len(r["series"]["12m"]) <= 53 and max(v for _, v in r["series"]["12m"]) == 100
    assert 12 <= len(r["series"]["3m"]) <= 14 and r["provider"] == "google"
    fake.calls.clear()
    assert asyncio.run(trends.get_trends(conn, "story", "US"))["cached"] and fake.calls == []


def test_low_resolution_fetches_12m(conn, fake):
    fake.values = lambda i, n: 100 if n == 261 and i < 50 else 3  # this year is only ≈ 3% of the 5-year peak
    r = asyncio.run(trends.get_trends(conn, "story", "US"))
    assert fake.calls == ["today 5-y", "today 12-m"] and r["derived"] == ["3m"]

    fake.calls.clear()
    fake.blocked = {"today 12-m"}  # the extra call is blocked: 12 months is still derived, no error
    r = asyncio.run(trends.get_trends(conn, "other", "US"))
    assert r["errors"] == {} and r["derived"] == ["12m", "3m"]


def test_compare_respects_cooldown(conn, fake):
    fake.blocked = {"today 5-y"}
    r = asyncio.run(trends.compare_trends(conn, ["a", "b"], "VN"))
    assert r["errors"] and trends.cooldown_until(conn)
    fake.calls.clear()
    asyncio.run(trends.compare_trends(conn, ["a", "b"], "VN"))
    assert fake.calls == []


def test_derive_12m_common_scale():
    pts = {"a": [["2025-01-05", 10], ["2026-01-04", 20], ["2026-10-11", 40]], "b": [["2026-10-11", 10]]}
    out = trends.derive_12m(pts)
    assert out == {"a": [["2026-01-04", 50], ["2026-10-11", 100]], "b": [["2026-10-11", 25]]}


def test_refetch_trends_updates_saved_run(conn, fake, tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = {**raw, "trends": {"series": {"3m": [["2026-10-01", 5]]}, "errors": {"5y": trends.THROTTLED}}}
    rid = run_repo.insert(conn, raw=raw, result=run_svc._analyze(raw, "History", []), category="History", affiliates=[])
    assert run_repo.get(conn, rid)["result"]["trends"]["class"] is None

    fake.blocked = {"today 5-y"}  # still blocked: the old 3-month series is kept
    got = run_svc.refetch_trends(conn, rid)
    assert run_repo.get(conn, rid, with_raw=True)["raw"]["trends"]["series"] == {"3m": [["2026-10-01", 5]]}
    assert set(got["errors"]) == {"5y", "12m"}

    conn.execute("DELETE FROM api_cache WHERE key = ?", (trends.COOLDOWN_KEY,))
    fake.blocked = set()
    got = run_svc.refetch_trends(conn, rid)
    run = run_repo.get(conn, rid, with_raw=True)
    assert got["errors"] == {} and run["raw"]["trends"]["series"]["5y"]
    assert run["result"]["trends"]["class"] is not None


def test_pytrends_modern_429_is_detected():
    from pytrends_modern import exceptions

    from yt_niche.sources import trends

    assert trends._is_429(exceptions.TooManyRequestsError("Too many requests"))
    assert trends.TrendReq.__module__ == "pytrends_modern.request"


def test_429_retries_with_fresh_cookie(monkeypatch):
    made = []

    class Client:
        def __init__(self, **kw):
            made.append(kw)

        def suggestions(self, q):
            if len(made) == 1:  # first session (bad cookie) is blocked, the new one passes
                raise TooManyRequestsError("Google returned a response with code 429")
            return [{"title": "Dog training", "type": "Topic"}]

    monkeypatch.setattr(trends, "TrendReq", Client)
    monkeypatch.setattr(trends, "RETRY_WAITS", (0, 0))
    trends.stats["rescued"] = 0
    assert trends.entity_types("dog training") == [{"title": "Dog training", "type": "Topic"}]
    assert len(made) == 2 and trends.stats["rescued"] == 1
    # the same browser User-Agent for the cookie fetch and the data calls
    assert not made[0]["rotate_user_agent"] and made[0]["requests_args"]["headers"]["User-Agent"] == trends.USER_AGENT

    made.clear()
    monkeypatch.setattr(Client, "suggestions", lambda self, q: (_ for _ in ()).throw(TooManyRequestsError("429")))
    with pytest.raises(trends.Throttled):
        trends.entity_types("dog training")
    assert len(made) == 3  # 1 call + 2 retries, a new session each time
