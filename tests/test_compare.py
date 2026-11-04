"""Lesson 2.4: 7-column niche comparison, multi-market opportunity score."""

import json
from pathlib import Path

import pytest

from yt_niche import config as cfg
from yt_niche.core import compare as cmp
from yt_niche.core import market
from yt_niche.core.confidence import HIGH, LOW, MEDIUM
from yt_niche.core.niche_score import LABEL_GREEN, LABEL_POTENTIAL, LABEL_RED
from yt_niche.core.trends_classify import FALLING, STABLE
from yt_niche.db import runs as run_repo
from yt_niche.services import markets as mk_svc
from yt_niche.services import report
from yt_niche.services.runs import _analyze
from yt_niche.sources import rpm as rpm_source
from yt_niche.sources.youtube import QuotaExceeded, YouTubeError

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture
def rpm_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")


def run(rid, name, score, *, conf=HIGH, n=50, median=10_000, outliers=10, booms=5, trend=STABLE, income=(10, 20),
        affiliates=0, fit=None, region="VN", created="2026-10-17T01:00:00+00:00"):
    return {
        "id": rid,
        "topic": name,
        "region": region,
        "window_days": 30,
        "fmt": "any",
        "created_at": created,
        "affiliates": ["x"] * affiliates,
        "result": {
            "params": {"fmt": "any"},
            "score": score,
            "demand": 60, "opportunity": 50, "freshness": 40, "competition": 10,
            "labels": [LABEL_GREEN if score >= 70 else LABEL_POTENTIAL if score >= 50 else LABEL_RED],
            "confidence": conf,
            "n": n,
            "median_views": median,
            "outlier_counts": {"total": outliers},
            "small_boom_channels": [{}] * booms,
            "trends": {"class": trend, "trend_12m": 0.1, "trend_5y": 0.2},
            "income_per_video": income,
            "format_split": {"fit": fit},
        },
    }


# ---------------------------------------------------------------------------
# Comparing niches
# ---------------------------------------------------------------------------


def test_gap_thresholds():
    assert cmp.gap_verdict(20) == cmp.GAP_CLEAR  # Buddhism 71 vs healing meditation 51
    assert cmp.gap_verdict(10) == cmp.GAP_CLEAR
    assert cmp.gap_verdict(7) == cmp.GAP_MODERATE
    assert cmp.gap_verdict(4) == cmp.GAP_TIE
    assert cmp.gap_verdict(-3) == cmp.GAP_NOISE


def test_clear_leader_lesson_example():
    a = run(1, "Buddhism", 71, median=23_600, outliers=21, booms=18)
    b = run(2, "healing meditation", 51, median=3_000, outliers=2, booms=4, fit="shorts")
    out = cmp.compare([a, b], ability="long")
    assert out["leader"]["name"] == "Buddhism" and f"20 points ahead of healing meditation ({cmp.GAP_CLEAR})" in out["leader_reason"]
    assert out["view_ratio"] == 7.9
    assert "median views 7.9× higher" in out["differentiator"]
    assert out["doors"] == ["healing meditation: still 4 small booming channels, fits Shorts"]
    assert out["best"]["median_views"] == {1} and out["best"]["format"] == {1}


def test_tie_decided_by_trends_then_small_channels():
    # Lesson 2.4 question: X 68, Y 65, medium confidence → tie, decided by Trends / small booming channels / money beyond ads.
    x = run(1, "X", 68, conf=MEDIUM, trend=FALLING, booms=8)
    y = run(2, "Y", 65, conf=MEDIUM, trend=STABLE, booms=3)
    out = cmp.compare([x, y])
    assert out["leader"]["name"] == "Y" and "tied" in out["leader_reason"]
    assert out["pairs"][0]["verdict"] == cmp.GAP_NOISE

    x2 = run(1, "X", 68, trend=STABLE, booms=8)
    assert cmp.compare([x2, {**y, "result": {**y["result"], "confidence": HIGH}}])["leader"]["name"] == "X"


def test_low_confidence_excluded_and_warnings():
    a = run(1, "A", 80, conf=LOW, n=12)
    b = run(2, "B", 60, n=50, booms=1)
    c = run(3, "C", 55, n=20, region="US", created="2026-09-01T00:00:00+00:00")
    out = cmp.compare([a, b, c])
    assert [r["name"] for r in out["excluded"]] == ["A"]
    assert [r["name"] for r in out["rows"]] == ["B", "C"]
    text = " | ".join(out["warnings"])
    assert "market" in text and "days apart" in text and "Sample sizes differ" in text
    assert out["big_channel_niches"] == ["B"] and "big-channel" in out["risk"]


# ---------------------------------------------------------------------------
# Opportunity score
# ---------------------------------------------------------------------------


def _weekly(values):
    return {"5y": [[f"w{i}", v] for i, v in enumerate(values)]}


def test_opportunity_formulas():
    rpm_us, rpm_br = (2.0, 4.0), (0.5, 1.0)
    no_trends = market.opportunity(70, rpm_us, None)
    money = market.money_level(rpm_us)
    assert no_trends["score"] == round(0.6 * 70 + 0.4 * money, 1) and "no Trends" in no_trends["formula"]

    flat = _weekly([50] * 260)
    with_trends = market.opportunity(70, rpm_us, flat)
    assert with_trends["search"] == 100
    assert with_trends["score"] == round(0.45 * 70 + 0.30 * 100 + 0.25 * money, 1)
    assert with_trends["green"]

    # The US beats Brazil even though Brazil watches more: ad prices are ~4× higher.
    assert market.opportunity(60, rpm_us, None)["score"] > market.opportunity(66, rpm_br, None)["score"]
    assert market.opportunity(70, None, flat)["score"] is None


def test_search_level_falling_and_sparse():
    falling = _weekly([100] * 208 + [20] * 52)
    assert market.search_level(falling) == 20
    assert market.search_level(_weekly([0] * 230 + [0, 5] * 15)) is None
    assert market.search_level(None) is None


def test_rpm_table_backfills_new_markets(rpm_tmp):
    old = rpm_source.default_table()
    rpm_source.save_table(old[old["market"].isin(["VN", "US"])])
    assert rpm_source.lookup("MX", "History") is not None
    assert set(rpm_source.load_table()["market"]) == set(cfg.MARKETS)


# ---------------------------------------------------------------------------
# Multi-market service
# ---------------------------------------------------------------------------


def test_market_batch_flow(conn, rpm_tmp, monkeypatch):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []

    def fake_dig(conn_, params, category=None, **kw):
        calls.append(params.region)
        if params.region == "BR":
            raise YouTubeError("search error 400")
        if params.region == "JP":
            raise QuotaExceeded("out of quota", "quotaExceeded")
        r = {**raw, "params": {**raw["params"], "topic": params.topic, "region": params.region}}
        return run_repo.insert(conn_, raw=r, result=_analyze(r, category, []), category=category, affiliates=[])

    monkeypatch.setattr(mk_svc, "dig", fake_dig)

    with pytest.raises(mk_svc.MarketError):
        mk_svc.create_batch(conn, "cầu nguyện", {"VN": "cầu nguyện", "US": " "}, category=None, window_days=30, fmt="any")
    bid = mk_svc.create_batch(
        conn,
        "cầu nguyện buổi sáng",
        {"VN": "cầu nguyện buổi sáng", "US": "morning prayer", "BR": "oração da manhã", "JP": "朝の祈り", "MX": "oración"},
        category="Spirituality / religion",
        window_days=30,
        fmt="any",
    )
    out = mk_svc.run_batch(conn, bid)
    assert calls == ["VN", "US", "BR", "JP"]  # quota ran out at JP, so it stops; MX is not dug
    assert out["done"] == 2 and out["stopped"]

    batch = mk_svc.load_batch(conn, bid)
    done = [it for it in batch["items"] if it.get("result")]
    assert [it["region"] for it in done] == ["US", "VN"]  # the US has the higher opportunity thanks to RPM
    assert done[0]["opportunity"]["score"] > done[1]["opportunity"]["score"]
    errors = {it["region"]: it["error"] for it in batch["items"] if not it.get("result")}
    assert errors["BR"].startswith("search error") and "quota" in errors["JP"].lower() and errors["MX"] == ""
    assert mk_svc.list_batches(conn)[0]["n_done"] == 2

    prompt = report.build_market_compare_prompt(batch, "can write English")
    assert "| United States | morning prayer |" in prompt and "{" not in prompt
    native = report.build_native_prompt(done[0], batch["topic"])
    assert "United States" in native and "{" not in native

    mk_svc.delete_batch(conn, bid)
    assert mk_svc.load_batch(conn, bid) is None and run_repo.list_all(conn)  # the per-country digs are kept


def test_translations_and_compare_prompt():
    text = '- US: "morning prayer"\nJP：無効\nbr: oração da manhã\nXX: skip\nno colon here'
    assert report.parse_translations(text) == {"US": "morning prayer", "BR": "oração da manhã"}
    p = report.build_translate_prompt("cầu nguyện buổi sáng", ["US", "JP"])
    assert "US:" in p and "Japan" in p and "{" not in p

    runs = [run(1, "A", 71), run(2, "B", 51, conf=LOW)]
    out = cmp.compare(runs)
    prompt = report.build_compare_prompt(out, runs, "faceless")
    assert f"| A | 71 ({LABEL_GREEN})" in prompt and "| B | 51" in prompt and "{" not in prompt
