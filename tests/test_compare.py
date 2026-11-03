"""Bài 2.4: so ngách 7 cột, điểm cơ hội đa thị trường."""

import json
from pathlib import Path

import pytest

from yt_niche import config as cfg
from yt_niche.core import compare as cmp
from yt_niche.core import market
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


def run(rid, name, score, *, conf="cao", n=50, median=10_000, outliers=10, booms=5, trend="bền", income=(10, 20),
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
            "labels": ["Ngách xanh" if score >= 70 else "Ngách tiềm năng" if score >= 50 else "Ngách đỏ"],
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
# So ngách
# ---------------------------------------------------------------------------


def test_gap_thresholds():
    assert cmp.gap_verdict(20) == "khác biệt rõ"  # Phật pháp 71 vs thiền chữa lành 51
    assert cmp.gap_verdict(10) == "khác biệt rõ"
    assert cmp.gap_verdict(7).startswith("chênh vừa")
    assert cmp.gap_verdict(4) == "coi như ngang, quyết bằng cột khác"
    assert cmp.gap_verdict(-3) == "nhiễu, coi như ngang"


def test_clear_leader_lesson_example():
    a = run(1, "Phật pháp", 71, median=23_600, outliers=21, booms=18)
    b = run(2, "thiền chữa lành", 51, median=3_000, outliers=2, booms=4, fit="shorts")
    out = cmp.compare([a, b], ability="long")
    assert out["leader"]["name"] == "Phật pháp" and "hơn thiền chữa lành 20 điểm (khác biệt rõ)" in out["leader_reason"]
    assert out["view_ratio"] == 7.9
    assert "lượt xem trung vị gấp 7.9 lần" in out["differentiator"]
    assert out["doors"] == ["thiền chữa lành: còn 4 kênh nhỏ nổ, hợp Shorts"]
    assert out["best"]["median_views"] == {1} and out["best"]["format"] == {1}


def test_tie_decided_by_trends_then_small_channels():
    # Câu hỏi bài 2.4: X 68, Y 65, tin cậy vừa → ngang, quyết bằng Trends / kênh nhỏ nổ / thu ngoài quảng cáo.
    x = run(1, "X", 68, conf="vừa", trend="đang rơi", booms=8)
    y = run(2, "Y", 65, conf="vừa", trend="bền", booms=3)
    out = cmp.compare([x, y])
    assert out["leader"]["name"] == "Y" and "ngang điểm" in out["leader_reason"]
    assert out["pairs"][0]["verdict"] == "nhiễu, coi như ngang"

    x2 = run(1, "X", 68, trend="bền", booms=8)
    assert cmp.compare([x2, {**y, "result": {**y["result"], "confidence": "cao"}}])["leader"]["name"] == "X"


def test_low_confidence_excluded_and_warnings():
    a = run(1, "A", 80, conf="thấp", n=12)
    b = run(2, "B", 60, n=50, booms=1)
    c = run(3, "C", 55, n=20, region="US", created="2026-09-01T00:00:00+00:00")
    out = cmp.compare([a, b, c])
    assert [r["name"] for r in out["excluded"]] == ["A"]
    assert [r["name"] for r in out["rows"]] == ["B", "C"]
    text = " | ".join(out["warnings"])
    assert "thị trường" in text and "cách nhau" in text and "Cỡ mẫu chênh" in text
    assert out["big_channel_niches"] == ["B"] and "kênh lớn" in out["risk"]


# ---------------------------------------------------------------------------
# Điểm cơ hội
# ---------------------------------------------------------------------------


def _weekly(values):
    return {"5y": [[f"w{i}", v] for i, v in enumerate(values)]}


def test_opportunity_formulas():
    rpm_us, rpm_br = (2.0, 4.0), (0.5, 1.0)
    no_trends = market.opportunity(70, rpm_us, None)
    money = market.money_level(rpm_us)
    assert no_trends["score"] == round(0.6 * 70 + 0.4 * money, 1) and "chưa có Trends" in no_trends["formula"]

    flat = _weekly([50] * 260)
    with_trends = market.opportunity(70, rpm_us, flat)
    assert with_trends["search"] == 100
    assert with_trends["score"] == round(0.45 * 70 + 0.30 * 100 + 0.25 * money, 1)
    assert with_trends["green"]

    # Mỹ thắng Brazil dù Brazil xem nhiều hơn: giá quảng cáo gấp ~4 lần.
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
    assert rpm_source.lookup("MX", "Lịch sử") is not None
    assert set(rpm_source.load_table()["market"]) == set(cfg.MARKETS)


# ---------------------------------------------------------------------------
# Service đa thị trường
# ---------------------------------------------------------------------------


def test_market_batch_flow(conn, rpm_tmp, monkeypatch):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []

    def fake_dig(conn_, params, category=None, **kw):
        calls.append(params.region)
        if params.region == "BR":
            raise YouTubeError("search lỗi 400")
        if params.region == "JP":
            raise QuotaExceeded("hết", "quotaExceeded")
        r = {**raw, "params": {**raw["params"], "topic": params.topic, "region": params.region}}
        return run_repo.insert(conn_, raw=r, result=_analyze(r, category, []), category=category, affiliates=[])

    monkeypatch.setattr(mk_svc, "dig", fake_dig)

    with pytest.raises(mk_svc.MarketError):
        mk_svc.create_batch(conn, "cầu nguyện", {"VN": "cầu nguyện", "US": " "}, category=None, window_days=30, fmt="any")
    bid = mk_svc.create_batch(
        conn,
        "cầu nguyện buổi sáng",
        {"VN": "cầu nguyện buổi sáng", "US": "morning prayer", "BR": "oração da manhã", "JP": "朝の祈り", "MX": "oración"},
        category="Tâm linh / tôn giáo",
        window_days=30,
        fmt="any",
    )
    out = mk_svc.run_batch(conn, bid)
    assert calls == ["VN", "US", "BR", "JP"]  # hết quota ở JP thì dừng, MX chưa đào
    assert out["done"] == 2 and out["stopped"]

    batch = mk_svc.load_batch(conn, bid)
    done = [it for it in batch["items"] if it.get("result")]
    assert [it["region"] for it in done] == ["US", "VN"]  # Mỹ cơ hội cao hơn nhờ RPM
    assert done[0]["opportunity"]["score"] > done[1]["opportunity"]["score"]
    errors = {it["region"]: it["error"] for it in batch["items"] if not it.get("result")}
    assert errors["BR"].startswith("search lỗi") and errors["JP"] == "hết quota" and errors["MX"] == ""
    assert mk_svc.list_batches(conn)[0]["n_done"] == 2

    prompt = report.build_market_compare_prompt(batch, "viết được tiếng Anh")
    assert "| Mỹ | morning prayer |" in prompt and "{" not in prompt
    native = report.build_native_prompt(done[0], batch["topic"])
    assert "người xem bản xứ Mỹ" in native and "{" not in native

    mk_svc.delete_batch(conn, bid)
    assert mk_svc.load_batch(conn, bid) is None and run_repo.list_all(conn)  # lần đào từng nước vẫn giữ


def test_translations_and_compare_prompt():
    text = '- US: "morning prayer"\nJP：無効\nbr: oração da manhã\nXX: bỏ\nkhông có dấu hai chấm'
    assert report.parse_translations(text) == {"US": "morning prayer", "BR": "oração da manhã"}
    p = report.build_translate_prompt("cầu nguyện buổi sáng", ["US", "JP"])
    assert "US: <từ khoá>" in p and "Nhật Bản" in p and "{" not in p

    runs = [run(1, "A", 71), run(2, "B", 51, conf="thấp")]
    out = cmp.compare(runs)
    prompt = report.build_compare_prompt(out, runs, "không lộ mặt")
    assert "| A | 71 (Ngách xanh)" in prompt and "| B | 51" in prompt and "{" not in prompt
