"""Các mục bổ sung sau khi đối chiếu khóa học: phiếu 7 ô, lời khuyên, nhận xét kênh, bình luận câu hỏi,
so Trends mẹ–con, từ khoá vào kế hoạch 10 video, nhắc đào lại hằng tháng, checklist chặng."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from yt_niche import config as cfg
from yt_niche.core import reading
from yt_niche.core import subniche as sn
from yt_niche.core.questions import is_question, question_comments
from yt_niche.db import runs as run_repo
from yt_niche.services import keywords as kw_svc
from yt_niche.services import report, scorecards, tracking
from yt_niche.services import test_plans as plan_svc
from yt_niche.services.runs import _analyze
from yt_niche.sources import trends as trends_source
from yt_niche.sources.youtube import YouTubeClient

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def rpm_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")


def _insert(conn, raw, topic="kể chuyện lịch sử", **kw):
    r = {**raw, "params": {**raw["params"], "topic": topic, **kw}}
    return run_repo.insert(conn, raw=r, result=_analyze(r, "Lịch sử", []), category="Lịch sử", affiliates=[])


# ---------------------------------------------------------------------------
# Đọc kết quả (bài 2.1, 2.2)
# ---------------------------------------------------------------------------


def test_advice_texts():
    assert "ngách con và từ khoá dài" in reading.zone_advice(70)
    assert "2–3 ngách con" in reading.zone_advice(69)
    assert "đổi thị trường" in reading.zone_advice(49)
    assert "Nới khoảng thời gian" in reading.confidence_advice("thấp")
    assert "mở vài kênh thật" in reading.confidence_advice("vừa")
    assert "10 triệu" in reading.format_advice("shorts") and "Shorts làm mồi" in reading.format_advice("long")
    assert reading.format_advice(None) is None
    assert "4.000 giờ" in reading.MONETIZATION


def test_topic_warning():
    assert reading.topic_warning("lịch sử", "vi") is None
    assert reading.topic_warning("history", "en") and "2–4 từ" in reading.topic_warning("history", "en")
    assert reading.topic_warning("tâm lý học về tiền cho mẹ bỉm sữa miền trung", "vi")
    assert reading.topic_warning("朝の祈り", "ja") is None


def test_seven_cells(conn, raw, rpm_tmp):
    run = run_repo.get(conn, _insert(conn, raw))
    sheet = reading.seven_cells(run)
    assert len(sheet["cells"]) == 7 and sheet["complete"], sheet["missing"]

    no_trends = {**raw, "trends": None}
    run2 = run_repo.get(conn, run_repo.insert(conn, raw=no_trends, result=_analyze(no_trends, None, []), category=None, affiliates=[]))
    assert reading.seven_cells(run2)["missing"] == ["Hình dạng Trends"]


def test_channel_notes_and_prompt(conn, raw, rpm_tmp):
    rid = _insert(conn, raw)
    cid = run_repo.get(conn, rid)["result"]["small_boom_channels"][0]["channel_id"]
    run_repo.set_channel_note(conn, rid, cid, note="  kể như phim,   10 phút ", reup=False)
    run_repo.set_channel_note(conn, rid, "UCreup", note="", reup=True)
    run = run_repo.get(conn, rid)
    assert run["channel_notes"][cid] == {"note": "kể như phim, 10 phút", "reup": False}
    block = report.data_block(run)
    assert "kể như phim, 10 phút" in block and "re-up, không học theo" in block
    run_repo.set_channel_note(conn, rid, "UCreup", note="", reup=False)
    assert "UCreup" not in run_repo.get(conn, rid)["channel_notes"]


def test_format_variants(conn, raw, rpm_tmp):
    a = _insert(conn, raw)
    b = _insert(conn, raw, fmt="long")
    _insert(conn, raw, window_days=90)
    variants = run_repo.format_variants(conn, run_repo.get(conn, a))
    assert {v["id"] for v in variants} == {a, b}


# ---------------------------------------------------------------------------
# Bình luận dạng câu hỏi (bài 2.1)
# ---------------------------------------------------------------------------


def test_is_question():
    assert is_question("Ad ơi làm video về nhà Nguyễn được không")
    assert is_question("Tại sao vua Quang Trung mất sớm vậy")
    assert is_question("How did they build it")
    assert is_question("Thật không?")
    assert not is_question("Video hay quá, cảm ơn ad")
    assert not is_question("Các cụ đừng bao giờ ở chung với con cái. Có tiền bao nhiêu đừng cho con biết")
    assert not is_question("What a great video, thanks")


def test_question_comments_rank_and_dedupe():
    comments = [
        {"text": "Vì sao nhà Hồ thất bại nhanh vậy", "likes": 5, "replies": 0},
        {"text": "vì sao nhà Hồ thất bại nhanh vậy", "likes": 1, "replies": 0},
        {"text": "Hay?", "likes": 100, "replies": 0},
        {"text": "Làm video về Lý Thường Kiệt được không ad", "likes": 2, "replies": 3},
        {"text": "Tuyệt vời, xem mãi không chán", "likes": 50, "replies": 0},
    ]
    out = question_comments(comments)
    assert [c["text"][:6] for c in out] == ["Làm vi", "Vì sao"]


def test_comment_threads_client(conn):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["videoId"] == "off":
            return httpx.Response(403, json={"error": {"errors": [{"reason": "commentsDisabled"}], "message": "off"}})
        item = {"snippet": {"totalReplyCount": 2, "topLevelComment": {"snippet": {"textDisplay": "Vì sao?", "likeCount": 3}}}}
        return httpx.Response(200, json={"items": [item]})

    async def go():
        async with YouTubeClient(conn, api_key="k", transport=httpx.MockTransport(handler)) as yt:
            a = await yt.comment_threads("v1")
            b = await yt.comment_threads("off")
            await yt.comment_threads("v1")  # cache
            return a, b, yt.meter.total

    a, b, units = asyncio.run(go())
    assert a == [{"video_id": "v1", "text": "Vì sao?", "likes": 3, "replies": 2}] and b == [] and units == 2


# ---------------------------------------------------------------------------
# So Trends ngách mẹ với ngách con (bài 2.2)
# ---------------------------------------------------------------------------


def _pts(values):
    return [[f"d{i}", v] for i, v in enumerate(values)]


def test_compare_trends_marks_rising_child():
    series = {
        "5y": {"tâm lý học": _pts([50] * 260), "tâm lý học về tiền": _pts([5] * 200 + [20] * 60)},
        "12m": {"tâm lý học": _pts([50] * 52), "tâm lý học về tiền": _pts(list(range(5, 57)))},
    }
    rows = {r["topic"]: r for r in sn.compare_trends(series, "tâm lý học")}
    assert rows["tâm lý học"]["direction_12m"] == "đi ngang" and rows["tâm lý học"]["is_parent"]
    assert rows["tâm lý học về tiền"]["direction_12m"] == "đi lên" and rows["tâm lý học về tiền"]["good_candidate"]


def test_trends_compare_source_caches_success(conn, monkeypatch):
    calls = []

    def fake(topics, timeframes, geo, source, hl):
        calls.append(topics)
        return {"series": {n: {t: [] for t in topics} for n in timeframes}, "errors": {}, "throttled": False}

    monkeypatch.setattr(trends_source, "_fetch_many", fake)
    topics = ["a", "b", "b", "c", "d", "e", "f"]
    first = asyncio.run(trends_source.compare_trends(conn, topics, "VN"))
    second = asyncio.run(trends_source.compare_trends(conn, topics, "VN"))
    assert first["topics"] == ["a", "b", "c", "d", "e"] and second["cached"] and len(calls) == 1


# ---------------------------------------------------------------------------
# Từ khoá dài vào kế hoạch 10 video (bài 2.5)
# ---------------------------------------------------------------------------


@pytest.fixture
def board(conn, raw, rpm_tmp):
    ids = [_insert(conn, raw, topic=t) for t in ("kể chuyện lịch sử", "lịch sử thế giới")]
    bid = scorecards.create(conn, "B", ids)
    scorecards.lock(conn, bid)
    manual = {k: (4, "") for k in ("money_other", "production", "policy_risk", "passion", "long_keywords", "ad_price")}
    scorecards.set_scores(conn, bid, ids[0], manual)
    scorecards.set_scores(conn, bid, ids[1], {**manual, "passion": (2, "")})
    return bid, ids


def _seed_keywords(conn, seed, region, scores):
    kw_svc.add_manual(conn, seed, region, [k for k, _, _ in scores])
    for k, score, low in scores:
        conn.execute(
            "UPDATE keyword SET score = ?, low_data = ?, scored_at = 'x' WHERE region = ? AND keyword = ?",
            (score, int(low), region, k),
        )


def test_keywords_fill_plan_titles(conn, board):
    bid, ids = board
    _seed_keywords(
        conn, "kể chuyện lịch sử", "VN",
        [("bí mật nhà nguyễn", 70, False), ("vua quang trung đánh quân thanh", 60, False),
         ("lịch sử ít dữ liệu", 90, True), ("chuyện xưa", 40, False)],
    )
    kw_svc.set_picked(conn, "VN", ["vua quang trung đánh quân thanh"], True)
    main_run = run_repo.get(conn, ids[0])
    ideas = plan_svc.keyword_ideas(conn, main_run)
    assert [k["keyword"] for k in ideas] == ["vua quang trung đánh quân thanh", "bí mật nhà nguyễn"]

    from datetime import date

    plan_svc.create(conn, bid, ["a", "b", "c"], date(2026, 11, 2), 2)
    plan_svc.save_videos(conn, bid, [{"idx": 1, "title": "Đã có tiêu đề"}])
    assert plan_svc.fill_titles(conn, bid, [k["keyword"] for k in ideas]) == 2
    titles = [v["title"] for v in plan_svc.get(conn, bid)["videos"][:3]]
    assert titles == ["Đã có tiêu đề", "Vua quang trung đánh quân thanh", "Bí mật nhà nguyễn"]

    prompt = report.build_test_plan_prompt(scorecards.load(conn, bid), main_run, keywords=ideas)
    assert "- bí mật nhà nguyễn (70 điểm)" in prompt and "{" not in prompt


# ---------------------------------------------------------------------------
# Đào lại mỗi tháng + checklist chặng
# ---------------------------------------------------------------------------


def test_due_redigs_and_checklist(conn, board):
    bid, ids = board
    later = datetime.now(timezone.utc) + timedelta(days=cfg.REDIG_DAYS + 1)
    tracked = tracking.tracked(conn)
    assert [t["role"] for t in tracked] == [tracking.MAIN, tracking.BACKUP]
    assert tracking.due_redigs(conn) == []
    due = tracking.due_redigs(conn, now=later)
    assert {d["run"]["topic"] for d in due} == {"kể chuyện lịch sử", "lịch sử thế giới"}

    items = {i["label"]: i for i in tracking.stage_checklist(conn)}
    assert len(items) == 10
    assert items["Điền bảng chấm có trọng số, chốt trọng số trước khi chấm"]["ok"]
    assert items["Ghi phiếu 7 ô cho mỗi ngách"]["ok"]
    assert not items["Chốt ngách chính và dự phòng, viết lời hứa kênh một câu"]["ok"]  # chưa có lời hứa
    scorecards.set_promise(conn, bid, "Kênh giúp bạn hiểu sử Việt.", True)
    items = {i["label"]: i for i in tracking.stage_checklist(conn)}
    assert items["Chốt ngách chính và dự phòng, viết lời hứa kênh một câu"]["ok"]
    assert items[f"Lưu ngách và đào lại mỗi tháng ({cfg.REDIG_DAYS} ngày)"]["ok"]
    assert not tracking.stage_checklist(conn, now=later)[-1]["ok"]


def test_all_channels(raw):
    from yt_niche.core.niche_score import SIZE_SMALL, all_channels

    rows = all_channels(raw)
    assert len(rows) == len({v["channel_id"] for v in raw["videos"]})
    assert sum(r["n_videos"] for r in rows) == len(raw["videos"])
    assert rows == sorted(rows, key=lambda r: r["total_views"], reverse=True)
    assert any(r["size"] == SIZE_SMALL for r in rows)
