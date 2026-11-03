import json
from pathlib import Path

import pytest

from yt_niche.core import scorecard as sc
from yt_niche.db import runs as run_repo
from yt_niche.services import scorecards as svc
from yt_niche.services.runs import _analyze

W = sc.DEFAULT_WEIGHTS
FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


def scores(base=4, **overrides):
    return {**{k: base for k in W}, **overrides}


def item(i, name, s):
    return {"id": i, "name": name, "scores": s}


# ---------------------------------------------------------------------------
# Điểm chốt và luật chọn
# ---------------------------------------------------------------------------


def test_final_score_formula():
    assert sc.final_score(W, scores(5)) == 100
    assert sc.final_score(W, scores(1)) == 20
    assert sc.final_score(W, scores(4)) == 80
    assert sc.final_score(W, scores(4, demand=None)) is None


def test_zero_weight_criterion_is_ignored():
    w = {**W, "ad_price": 0, "money_other": 20}
    assert sc.weights_error(w) is None
    assert sc.final_score(w, scores(4, ad_price=None)) == 80
    assert sc.red_flags(w, scores(4, ad_price=1)) == []


def test_weights_validation():
    assert sc.weights_error(W) is None
    assert "100" in sc.weights_error({**W, "demand": 20})
    assert sc.weights_error({**W, "demand": -5, "trend": 30}) == "Trọng số không được âm"


def test_lesson_quiz_red_flag_loses_to_lower_score():
    """Bài 2.5, câu 1: P 84 điểm nhưng rủi ro 1/5, Q 76 điểm → chọn Q."""
    p = scores(4, demand=5, small_channels=5, trend=5, competition=5, policy_risk=1)
    q = scores(4, ad_price=2)
    assert sc.final_score(W, p) == 84 and sc.final_score(W, q) == 76
    d = sc.decide([item(1, "P", p), item(2, "Q", q)], W)
    assert d["main"]["name"] == "Q"
    assert d["ranking"][0]["name"] == "P" and d["ranking"][0]["red_flags"] == ["policy_risk"]
    assert d["backup"] is None  # P có cờ đỏ nên cũng không làm dự phòng


def test_lesson_example_money_psychology():
    """Ví dụ "Tiền & Tâm Lý": 81 / 72 / 71 → chính 81; dự phòng 72 vì Tiền ngoài quảng cáo cao hơn ngách 71.

    Trọng số mặc định: mỗi điểm của tiêu chí 15 → 3 điểm chốt, tiêu chí 10 → 2, tiêu chí 5 → 1. Tất cả 4 = 80.
    """
    money = scores(4, policy_risk=3, passion=5, trend=5)  # 80 - 2 + 1 + 2 = 81, rủi ro 3/5 như bài
    self_dev = scores(4, demand=3, small_channels=3, production=3, trend=3, money_other=5)  # 80 - 3 - 3 - 2 - 2 + 2
    love = scores(4, demand=3, small_channels=3, passion=3, money_other=3)  # 80 - 3 - 3 - 1 - 2
    assert [sc.final_score(W, s) for s in (money, self_dev, love)] == [81, 72, 71]
    d = sc.decide(
        [item(1, "tâm lý học về tiền", money), item(2, "phát triển bản thân", self_dev), item(3, "tâm lý tình yêu", love)], W
    )
    assert d["main"]["name"] == "tâm lý học về tiền" and "cao nhất" in d["main_reason"]
    assert d["backup"]["name"] == "phát triển bản thân" and "hoà điểm" in d["backup_reason"]


def test_tie_break_can_pick_lower_score():
    a = scores(4)  # 80, Sản xuất + Tiền ngoài QC = 8
    b = scores(4, demand=3, small_channels=3, production=5)  # 76, = 9
    assert sc.final_score(W, a) - sc.final_score(W, b) == 4
    d = sc.decide([item(1, "A", a), item(2, "B", b)], W)
    assert d["main"]["name"] == "B" and "hoà điểm" in d["main_reason"]
    assert d["backup"]["name"] == "A"


def test_gap_of_5_is_not_a_tie():
    a = scores(4)  # 80
    b = scores(4, demand=3, small_channels=3, trend=3, production=5, money_other=5, passion=3)  # 75
    assert sc.final_score(W, b) == 75
    d = sc.decide([item(1, "A", a), item(2, "B", b)], W)
    assert d["main"]["name"] == "A" and "cao nhất" in d["main_reason"]


def test_incomplete_board_has_no_decision():
    d = sc.decide([item(1, "A", scores(4, passion=None)), item(2, "B", scores(4))], W)
    assert not d["complete"] and d["main"] is None
    assert d["ranking"][-1]["name"] == "A"  # chưa chấm xong xếp cuối


def test_all_flagged_has_no_main():
    d = sc.decide([item(1, "A", scores(4, production=1)), item(2, "B", scores(4, passion=1))], W)
    assert d["complete"] and d["main"] is None


# ---------------------------------------------------------------------------
# Gợi ý tự động
# ---------------------------------------------------------------------------


def _run(rid, median, booms=(), competition=10, trend=None, rpm=None, tier=None, affiliates=(), category=None, created="2026-10-17"):
    return {
        "id": rid,
        "topic": f"n{rid}",
        "region": "VN",
        "window_days": 30,
        "fmt": "any",
        "created_at": created + "T00:00:00+00:00",
        "category": category,
        "affiliates": list(affiliates),
        "result": {
            "median_views": median,
            "small_boom_channels": [{"age_months": a} for a in booms],
            "competition": competition,
            "trends": trend or {"class": None},
            "rpm": rpm,
            "rpm_tier": tier,
        },
    }


def test_auto_suggestions():
    runs = [
        _run(1, 20_000, booms=[3] + [20] * 9, trend={"class": "bền", "direction": "đi lên"}, rpm=(1, 3), tier="vừa",
             affiliates=["a", "b", "c"]),
        _run(2, 5_000, booms=[20, 20], competition=50, trend={"class": "đang rơi"}, category="Tài chính / kinh doanh"),
    ]
    s = sc.auto_suggestions(runs)
    assert s[1]["demand"][0] == 5 and s[2]["demand"][0] == 3  # kém 4 lần → trừ 2
    assert s[1]["trend"][0] == 5 and s[2]["trend"][0] == 2
    assert s[1]["small_channels"][0] == 5 and s[2]["small_channels"][0] == 2
    assert s[1]["competition"][0] == 5 and s[2]["competition"][0] == 2
    assert s[1]["ad_price"][0] == 3 and s[2]["ad_price"][0] is None
    assert s[1]["money_other"][0] == 4 and s[2]["money_other"][0] is None
    assert s[1]["production"][0] is None
    assert "nhạy cảm" in s[2]["policy_risk"][1]


def test_young_boom_bonus():
    s = sc.auto_suggestions([_run(1, 1000, booms=[3, 20, 20])])
    assert s[1]["small_channels"][0] == 4  # 3 kênh → 3, có kênh 3 tháng tuổi → +1


def test_setup_warnings():
    a = _run(1, 1000)
    b = {**_run(2, 1000, created="2026-10-25"), "region": "US"}
    w = sc.setup_warnings([a, b])
    assert any("thị trường" in x for x in w) and any("8 ngày" in x for x in w)
    assert sc.setup_warnings([a, _run(3, 1000, created="2026-10-18")]) == []


# ---------------------------------------------------------------------------
# Service + DB
# ---------------------------------------------------------------------------


@pytest.fixture
def two_runs(conn, tmp_path, monkeypatch):
    from yt_niche import config as cfg

    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = []
    for topic, affiliates in (("kể chuyện lịch sử", ["Sách"]), ("lịch sử thế giới", [])):
        r = {**raw, "params": {**raw["params"], "topic": topic}}
        ids.append(run_repo.insert(conn, raw=r, result=_analyze(r, "Lịch sử", affiliates), category="Lịch sử", affiliates=affiliates))
    return ids


def test_board_lifecycle(conn, two_runs):
    with pytest.raises(svc.ScorecardError):
        svc.create(conn, "x", two_runs[:1])  # cần ít nhất 2 ngách

    bid = svc.create(conn, "Tháng 10", two_runs)
    data = svc.load(conn, bid)
    assert data["decision"] is None and not data["locked_at"]
    assert data["cells"][two_runs[0]]["money_other"]["score"] == 3  # 1 sản phẩm affiliate
    assert data["cells"][two_runs[0]]["production"]["score"] is None

    with pytest.raises(svc.ScorecardError, match="Khoá trọng số"):
        svc.set_scores(conn, bid, two_runs[0], {"production": (4, "")})
    with pytest.raises(svc.ScorecardError, match="100"):
        svc.set_weights(conn, bid, {**W, "demand": 30})

    svc.set_weights(conn, bid, {**W, "ad_price": 0, "money_other": 20})
    svc.lock(conn, bid)
    with pytest.raises(svc.ScorecardError, match="đã khoá"):
        svc.set_weights(conn, bid, W)

    manual = {"production": (4, "nhiều tư liệu"), "policy_risk": (4, ""), "passion": (5, ""), "long_keywords": (3, ""), "money_other": (3, "")}
    svc.set_scores(conn, bid, two_runs[0], manual)
    assert not svc.load(conn, bid)["decision"]["complete"]
    svc.set_scores(conn, bid, two_runs[1], {**manual, "passion": (1, "không thích")})
    data = svc.load(conn, bid)
    d = data["decision"]
    assert d["complete"] and d["main"]["name"] == "kể chuyện lịch sử" and d["backup"] is None
    assert data["cells"][two_runs[0]]["production"]["note"] == "nhiều tư liệu"

    v2 = svc.new_version(conn, bid)
    v2_data = svc.load(conn, v2)
    assert v2_data["version"] == 2 and v2_data["parent_id"] == bid and not v2_data["locked_at"]
    assert v2_data["weights"]["money_other"] == 20
    assert v2_data["cells"][two_runs[0]]["production"]["score"] == 4

    svc.set_promise(conn, bid, "  Lịch sử Việt kể như phim.  ", True)
    assert svc.load(conn, bid)["promise"] == "Lịch sử Việt kể như phim."

    svc.delete(conn, bid)
    assert conn.execute("SELECT COUNT(*) FROM scorecard_cell WHERE scorecard_id = ?", (bid,)).fetchone()[0] == 0
    assert [b["id"] for b in svc.list_all(conn)] == [v2]


def test_load_skips_deleted_runs(conn, two_runs):
    bid = svc.create(conn, "x", two_runs)
    run_repo.delete(conn, two_runs[1])
    data = svc.load(conn, bid)
    assert len(data["runs"]) == 1 and data["missing_runs"] == 1
