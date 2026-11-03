import json
from datetime import date
from pathlib import Path

import pytest

from yt_niche.core import test_plan as tp
from yt_niche.db import runs as run_repo
from yt_niche.services import report
from yt_niche.services import scorecards
from yt_niche.services import test_plans as svc
from yt_niche.services.runs import _analyze

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"
PILLARS = ["bẫy tâm lý khi tiêu tiền", "tâm lý tiết kiệm", "tiền và các mối quan hệ"]


def v(idx, views=None, impressions=None, ctr=None, retention=None, avd=None, asks=False, pillar=None):
    return {
        "idx": idx,
        "pillar": pillar or PILLARS[(idx - 1) % 3],
        "title": f"video {idx}",
        "views": views,
        "impressions": impressions,
        "ctr": ctr,
        "retention": retention,
        "avd_seconds": avd,
        "asks_more": asks,
    }


def ten(**kw):
    return [v(i, **kw) for i in range(1, 11)]


# ---------------------------------------------------------------------------
# Lập kế hoạch
# ---------------------------------------------------------------------------


def test_schedule_two_per_week_over_five_weeks():
    days = tp.schedule(date(2026, 11, 2), 2)
    assert len(days) == 10 and days[0] == date(2026, 11, 2)
    assert (days[-1] - days[0]).days < 35  # 10 video trong 5 tuần


def test_default_pillars_4_3_3():
    order = tp.default_pillar_order(PILLARS)
    assert [order.count(p) for p in PILLARS] == [4, 3, 3]


def test_plan_issues():
    videos = [{**x, "title": x["title"]} for x in ten()]
    assert tp.plan_issues(PILLARS, videos) == []
    assert tp.plan_issues(PILLARS[:2] + [""], videos)
    videos[0]["title"] = "x" * 71
    videos[1]["title"] = ""
    for i in range(10):
        videos[i]["pillar"] = PILLARS[0] if i < 8 else PILLARS[1]
    issues = " | ".join(tp.plan_issues(PILLARS, videos))
    assert "dài hơn 70" in issues and "chưa có tiêu đề: 2" in issues and "tâm lý tiết kiệm\" mới có 2" in issues


# ---------------------------------------------------------------------------
# Mốc video 5 và 10
# ---------------------------------------------------------------------------


def test_review_at_5_needs_five_videos():
    assert not tp.review_at_5([v(1, views=100), v(2, views=100)])["ready"]
    r = tp.review_at_5([v(i, views=100, impressions=2000, ctr=1.2, retention=45) for i in range(1, 6)])
    assert r["ready"] and any("tiêu đề và ảnh bìa" in a for a in r["actions"])
    assert not any("hook" in a for a in r["actions"])


def test_too_early_to_decide():
    r = tp.decide_at_10([v(i, views=100) for i in range(1, 4)], fixes_applied=False, out_of_ideas=False)
    assert not r["ready"] and r["decision"] is None and "Đừng kết luận" in r["reasons"][0]


def test_keep_when_standouts_and_growth():
    videos = ten(impressions=3000, ctr=4, retention=40)
    for i, x in enumerate(videos):
        x["views"], x["avd_seconds"] = 1000, 100 + i * 10
    videos[3]["views"] = videos[6]["views"] = 5000  # video 4, 7 (trụ 1) vượt rõ
    videos[5]["asks_more"] = True
    r = tp.decide_at_10(videos, fixes_applied=False, out_of_ideas=False)
    assert r["decision"] == tp.KEEP
    assert "Dồn thêm video vào trụ \"bẫy tâm lý khi tiêu tiền\"" in r["actions"][0]


def test_lesson_quiz_low_ctr_long_watch_keeps_niche_and_fixes_title():
    """Bài 2.5, câu 2: CTR thấp nhưng người xem ở lại lâu → giữ ngách, sửa tiêu đề và ảnh bìa."""
    r = tp.decide_at_10(ten(views=300, impressions=8000, ctr=1.0, retention=55, avd=300), fixes_applied=False, out_of_ideas=False)
    assert r["decision"] == tp.ADJUST
    assert any("tiêu đề và ảnh bìa" in a for a in r["actions"])
    assert not any("hook" in a for a in r["actions"])


def test_good_ctr_but_early_drop_fixes_hook():
    r = tp.decide_at_10(ten(views=300, impressions=8000, ctr=5, retention=15, avd=40), fixes_applied=False, out_of_ideas=False)
    assert r["decision"] == tp.ADJUST and any("hook" in a for a in r["actions"])


def test_switch_after_fixes_with_no_impressions():
    r = tp.decide_at_10(ten(views=20, impressions=100, ctr=3, retention=40, avd=60), fixes_applied=True, out_of_ideas=False)
    assert r["decision"] == tp.SWITCH and "hiển thị vẫn gần như không có" in r["reasons"][0]


def test_no_switch_without_fixing_first():
    r = tp.decide_at_10(ten(views=20, impressions=100, ctr=3, retention=40, avd=60), fixes_applied=False, out_of_ideas=False)
    assert r["decision"] == tp.ADJUST and any("Chưa đánh dấu đã chỉnh" in a for a in r["actions"])


def test_out_of_ideas_switches_anytime():
    r = tp.decide_at_10([v(1, views=100)], fixes_applied=False, out_of_ideas=True)
    assert r["decision"] == tp.SWITCH


def test_auto_drop_hints():
    old = {"result": {"trends": {"class": "bền"}, "small_boom_channels": [{}, {}]}}
    new = {"result": {"trends": {"class": "đang rơi"}, "small_boom_channels": []}}
    hints = tp.auto_drop_hints([new, old])
    assert len(hints) == 2 and "2 → 0" in hints[1]
    assert tp.auto_drop_hints([]) == []


# ---------------------------------------------------------------------------
# Service + prompt
# ---------------------------------------------------------------------------


@pytest.fixture
def decided_board(conn, tmp_path, monkeypatch):
    from yt_niche import config as cfg

    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ids = []
    for topic in ("kể chuyện lịch sử", "lịch sử thế giới"):
        r = {**raw, "params": {**raw["params"], "topic": topic}}
        ids.append(run_repo.insert(conn, raw=r, result=_analyze(r, "Lịch sử", []), category="Lịch sử", affiliates=[]))
    bid = scorecards.create(conn, "B", ids)
    scorecards.lock(conn, bid)
    manual = {k: (4, "") for k in ("money_other", "production", "policy_risk", "passion", "long_keywords", "ad_price")}
    scorecards.set_scores(conn, bid, ids[0], manual)
    scorecards.set_scores(conn, bid, ids[1], {**manual, "passion": (2, "")})
    scorecards.set_promise(conn, bid, "Lịch sử Việt kể như phim.", True)
    return bid, ids


def test_plan_service_lifecycle(conn, decided_board):
    bid, ids = decided_board
    with pytest.raises(svc.PlanError, match="3 trụ"):
        svc.create(conn, bid, ["a", "a", "b"], date(2026, 11, 2), 2)
    svc.create(conn, bid, PILLARS, date(2026, 11, 2), 2)
    plan = svc.get(conn, bid)
    assert plan["run_id"] == ids[0] and len(plan["videos"]) == 10
    assert plan["videos"][0]["planned_date"] == "2026-11-02"

    svc.save_videos(
        conn,
        bid,
        [
            {"idx": 1, "title": "  Vì sao tiêu tiền khi buồn  ", "published_at": date(2026, 11, 3), "views": 1200.0,
             "ctr": "4.5", "asks_more": True, "impressions": float("nan")},
            {"idx": 2, "planned_date": "NaT", "note": None},
        ],
    )
    plan = svc.get(conn, bid)
    v1, v2 = plan["videos"][:2]
    assert v1["title"] == "Vì sao tiêu tiền khi buồn" and v1["published_at"] == "2026-11-03"
    assert v1["views"] == 1200 and v1["ctr"] == 4.5 and v1["asks_more"] is True and v1["impressions"] is None
    assert v2["planned_date"] is None and v2["note"] == ""

    svc.rename_pillars(conn, bid, ["A", "B", "C"])
    assert {x["pillar"] for x in svc.get(conn, bid)["videos"]} == {"A", "B", "C"}

    svc.set_flags(conn, bid, fixes_applied=True, out_of_ideas=False, drop_flags=["hate_it"])
    assert svc.get(conn, bid)["drop_flags"] == ["hate_it"]
    with pytest.raises(svc.PlanError):
        svc.set_flags(conn, bid, fixes_applied=True, out_of_ideas=False, drop_flags=["lạ"])

    scorecards.delete(conn, bid)  # xoá bảng chấm thì xoá luôn kế hoạch
    assert svc.get(conn, bid) is None
    assert conn.execute("SELECT COUNT(*) FROM test_video").fetchone()[0] == 0


def test_plan_requires_main_niche(conn, decided_board):
    bid, ids = decided_board
    v2 = scorecards.new_version(conn, bid)  # chưa khoá → chưa có quyết định
    with pytest.raises(svc.PlanError, match="ngách chính"):
        svc.create(conn, v2, PILLARS, date(2026, 11, 2), 2)


def test_lesson_25_prompts(conn, decided_board):
    bid, ids = decided_board
    board = scorecards.load(conn, bid)
    review = report.build_scorecard_review_prompt(board)
    assert "| kể chuyện lịch sử |" in review and "Điểm chốt" in review and "{" not in review

    main_run = run_repo.get(conn, ids[0])
    plan_prompt = report.build_test_plan_prompt(board, main_run, per_week=2, channel_style="không lộ mặt")
    assert "Lịch sử Việt kể như phim." in plan_prompt and "5 tuần" in plan_prompt and "{" not in plan_prompt
