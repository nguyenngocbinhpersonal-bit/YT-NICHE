"""Chạy từng trang Streamlit trên DB tạm (không gọi API) để bắt lỗi hiển thị."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from yt_niche import config as cfg
from yt_niche.db import runs as run_repo
from yt_niche.db.connection import connect
from yt_niche.services.runs import _analyze
from yt_niche.ui import common

UI = Path(__file__).resolve().parent.parent / "yt_niche" / "ui"
FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")
    monkeypatch.setattr(cfg, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("COMMANDCODE_API_KEY", raising=False)  # không gọi AI thật khi máy đã lưu key
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)  # không gọi DataForSEO thật
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    db = connect(tmp_path / "app.db")
    common.get_conn.clear()
    monkeypatch.setattr(common, "connect", lambda: db)
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    run_id = run_repo.insert(db, raw=raw, result=_analyze(raw, "Lịch sử", []), category="Lịch sử", affiliates=[])
    yield run_id
    common.get_conn.clear()


def _run(page: str, **state) -> AppTest:
    at = AppTest.from_file(str(UI / "pages" / page), default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_dig_page_renders_current_run(app_db):
    at = _run("dig.py", current_run=app_db)
    assert any("kể chuyện lịch sử" in s.value for s in at.subheader)
    assert any(m.label == "Điểm ngách" for m in at.metric)


def test_dig_page_requires_topic(app_db):
    at = _run("dig.py")
    at.button[0].click().run()  # nút "Đào" của form
    assert any("Nhập chủ đề" in e.value for e in at.error)


def test_saved_page(app_db):
    at = _run("saved.py")
    assert at.dataframe  # bảng danh sách


def test_settings_page(app_db):
    at = _run("settings_page.py")
    assert at.tabs


def test_scorecard_page_full_flow(app_db):
    from yt_niche.core.scorecard import DEFAULT_WEIGHTS
    from yt_niche.services import scorecards as svc

    db = common.get_conn()
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw2 = {**raw, "params": {**raw["params"], "topic": "lịch sử thế giới"}}
    other = run_repo.insert(db, raw=raw2, result=_analyze(raw2, "Lịch sử", []), category="Lịch sử", affiliates=[])

    at = _run("scorecard_page.py")  # chưa có bảng: hiện form tạo
    assert any(m.label.startswith("Chọn 2–5") for m in at.multiselect)

    bid = svc.create(db, "Thử", [app_db, other])
    at = _run("scorecard_page.py", board=bid)  # chưa khoá: hiện ô trọng số
    assert len(at.number_input) == len(DEFAULT_WEIGHTS)

    svc.lock(db, bid)
    manual = {k: (4, "") for k in ("money_other", "production", "policy_risk", "passion", "long_keywords", "ad_price")}
    svc.set_scores(db, bid, app_db, manual)
    svc.set_scores(db, bid, other, {**manual, "passion": (2, "")})
    at = _run("scorecard_page.py", board=bid)
    assert any("Ngách chính" in m.value for m in at.markdown)


def test_test_plan_page_flow(app_db):
    from datetime import date

    from yt_niche.services import scorecards as sc_svc
    from yt_niche.services import test_plans as tp_svc

    db = common.get_conn()
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw2 = {**raw, "params": {**raw["params"], "topic": "lịch sử thế giới"}}
    other = run_repo.insert(db, raw=raw2, result=_analyze(raw2, "Lịch sử", []), category="Lịch sử", affiliates=[])

    at = _run("video_plan_page.py")  # chưa có bảng chấm nào chốt
    assert any("Bảng chấm trước" in i.value for i in at.info)

    bid = sc_svc.create(db, "Thử", [app_db, other])
    sc_svc.lock(db, bid)
    manual = {k: (4, "") for k in ("money_other", "production", "policy_risk", "passion", "long_keywords", "ad_price")}
    sc_svc.set_scores(db, bid, app_db, manual)
    sc_svc.set_scores(db, bid, other, {**manual, "passion": (2, "")})

    at = _run("video_plan_page.py", board=bid)  # chưa có kế hoạch: form tạo
    assert any(t.label == "Trụ 1" for t in at.text_input)

    tp_svc.create(db, bid, ["A", "B", "C"], date(2026, 11, 2), 2)
    tp_svc.save_videos(db, bid, [{"idx": i, "views": 100 * i, "ctr": 3.0, "impressions": 2000, "retention": 40} for i in range(1, 6)])
    at = _run("video_plan_page.py", board=bid)
    assert any("Mốc video 5" in m.value for m in at.markdown)


def _second_run(topic: str, region: str = "VN", parent_id=None) -> int:
    db = common.get_conn()
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw = {**raw, "params": {**raw["params"], "topic": topic, "region": region}}
    return run_repo.insert(
        db, raw=raw, result=_analyze(raw, "Lịch sử", []), category="Lịch sử", affiliates=[],
        parent_id=parent_id, layer=2 if parent_id else 1,
    )


def test_subniche_page(app_db):
    child = _second_run("lịch sử nhật thời edo", parent_id=app_db)
    at = _run("subniche_page.py", sub_run=app_db)
    assert len(at.tabs) == 4
    assert any("Cụm lặp lại" in m.value for m in at.markdown)
    at = _run("subniche_page.py", sub_run=child)
    form_text = [t for t in at.text_input if t.label == "Lời hứa kênh một câu"][0]
    form_text.input("Kênh giúp bạn hiểu Nhật thời Edo.")
    [t for t in at.text_area if "ý tưởng video" in t.label][0].input("Ý một\nÝ hai")
    [b for b in at.button if b.label == "Lưu và kiểm tra"][0].click().run()
    assert not at.exception
    assert any("2 ý không trùng / 2 dòng" in m.value for m in at.markdown)


def test_compare_page(app_db):
    from yt_niche.services import markets as mk_svc

    us = _second_run("world history stories", region="US")
    db = common.get_conn()
    bid = mk_svc.create_batch(
        db, "kể chuyện lịch sử", {"VN": "kể chuyện lịch sử", "US": "world history stories"},
        category="Lịch sử", window_days=30, fmt="any",
    )
    db.execute("UPDATE market_batch_item SET run_id = ? WHERE batch_id = ? AND region = 'VN'", (app_db, bid))
    db.execute("UPDATE market_batch_item SET run_id = ? WHERE batch_id = ? AND region = 'US'", (us, bid))
    at = _run("compare_page.py", batch=bid)
    assert len(at.tabs) == 3
    assert any("Kết luận 3 câu" in m.value for m in at.markdown)
    assert len(at.dataframe) >= 3  # bảng 7 cột, chênh cặp, bảng đa thị trường


def test_progress_page(app_db):
    at = _run("progress_page.py")
    assert any("mục đã đạt" in (p.text or "") for p in at.get("progress")) or at.markdown


def test_run_view_shows_sheet_and_notes(app_db):
    at = _run("dig.py", current_run=app_db)
    assert any("Phiếu 7 ô" in e.label for e in at.expander)
    assert any(t.label.startswith("Tất cả kênh (") for t in at.tabs)
    assert any("Link video" in d.value.columns for d in at.dataframe)
    note = [t for t in at.text_input if t.label == "Điểm lặp lại"][0]
    note.input("kể như phim").run()
    assert not at.exception
    assert any(v.get("note") == "kể như phim" for v in run_repo.get(common.get_conn(), app_db)["channel_notes"].values())


def test_radar_page(app_db, monkeypatch):
    from yt_niche.services import radar
    from yt_niche.sources import trends

    tree = {"id": 0, "name": "All", "children": [{"id": 66, "name": "Pets & Animals"}]}
    monkeypatch.setattr(trends, "category_tree", lambda conn: tree)
    monkeypatch.setattr(radar, "task_status", lambda: False)
    common.get_conn().execute(
        """INSERT INTO radar_keyword (region, keyword, category_id, kind, value, first_seen, last_seen, status,
               trend_json, potential)
           VALUES ('US', 'dog training tips', 66, 'rising', 900, '2026-10-18T00:00:00+00:00',
                   '2026-10-18T00:00:00+00:00', 'passed', '{"ok": true, "durability": 100, "class": "bền"}', 70)"""
    )
    at = _run("radar_page.py")
    assert len(at.tabs) == 5
    assert any("dog training tips" in d.value["Từ khoá"].tolist() for d in at.dataframe if "Từ khoá" in d.value.columns)
    links = next(d.value for d in at.dataframe if "Trends YouTube" in d.value.columns)
    assert links["Trends YouTube"][0] == (
        "https://trends.google.com/trends/explore?q=dog+training+tips&geo=US&date=today+12-m&gprop=youtube"
    )
    assert "Trends Google" not in links.columns and "Trends 5 năm" not in links.columns


def test_settings_markets_tab(app_db):
    at = _run("settings_page.py")
    assert any(t.label == "🌐 Thị trường" for t in at.tabs)


def test_settings_ai_tab_and_prompt_button(app_db, monkeypatch):
    from yt_niche.services import ai

    db = common.get_conn()
    at = _run("settings_page.py")
    assert any(t.label == "🤖 AI" for t in at.tabs)
    assert any(b.label == "Lưu cài đặt AI" for b in at.button)
    # Bật AI: trang đào hiện nút chạy prompt, bấm thì hiện câu trả lời (AI giả)
    monkeypatch.setenv("COMMANDCODE_API_KEY", "sk-test-1234567890")
    monkeypatch.setattr(ai, "_post", lambda url, payload: {"choices": [{"message": {"content": "Nhận xét mẫu từ AI"}}]})
    ai.save_config(db, {"enabled": True, "model_fast": "f", "model_deep": "d"})
    at = _run("dig.py", current_run=app_db)
    btn = next(b for b in at.button if b.label == "🤖 Chạy bằng AI")
    btn.click().run()
    assert any("Nhận xét mẫu từ AI" in m.value for m in at.markdown)


def test_settings_google_trends_tab(app_db, monkeypatch):
    from yt_niche.sources import dataforseo as dfs

    at = _run("settings_page.py")
    assert any(t.label == "📈 Google Trends" for t in at.tabs)
    btn = next(b for b in at.button if b.label == "Lưu cài đặt Google Trends")
    next(t for t in at.toggle if t.label == "Dùng DataForSEO khi Google chặn").set_value(True)
    btn.click().run()
    assert any("Cần lưu login" in e.value for e in at.error)  # chưa có login thì không bật
    assert not dfs.get_config(common.get_conn())["enabled"]


def test_similar_page_empty_and_with_run(app_db):
    from tests.test_similar import data_api, youtube_web
    from yt_niche.services import similar

    at = _run("similar_page.py")  # chưa có lần tra: chỉ có form
    assert at.text_input

    db = common.get_conn()
    rid = similar.find_similar(
        db, "https://www.youtube.com/watch?v=SRCVIDEO001", "US", api_key="k",
        yt_transport=data_api({}), it_transport=youtube_web([]),
    )
    at = _run("similar_page.py", similar_run=rid)
    assert any("What if you were 10 times stronger" in s.value for s in at.subheader)
    assert any(m.label == "Kênh tương tự" and m.value == "2" for m in at.metric)
    assert len(at.dataframe) == 2  # bảng kênh và bảng video
