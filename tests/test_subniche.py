"""Bài 2.3: ngách con, kiểm tra đủ sống, từ khoá dài."""

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from yt_niche import config as cfg
from yt_niche.core import keyword_score as ks
from yt_niche.core import scorecard as sc
from yt_niche.core import subniche as sn
from yt_niche.core.text import contains_all, tokens, word_count
from yt_niche.db import runs as run_repo
from yt_niche.db.connection import connect
from yt_niche.services import keywords as kw_svc
from yt_niche.services import report
from yt_niche.services import runs as run_svc
from yt_niche.services import subniches as sub_svc
from yt_niche.services.fetch import DigParams
from yt_niche.sources import suggest

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def rpm_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")


def _run(result_overrides=None, **kw):
    r = {
        "outlier_counts": {"total": 5},
        "small_boom_channels": [{}, {}],
        "confidence": "vừa",
        "rpm_tier": "vừa",
        "outliers": [],
    }
    r.update(result_overrides or {})
    return {"id": 1, "topic": "t", "layer": 2, "affiliates": [], "result": r, **kw}


# ---------------------------------------------------------------------------
# Tách từ, cụm lặp lại, phân loại gợi ý
# ---------------------------------------------------------------------------


def test_tokens_and_counts():
    assert tokens("Vì sao TIÊU TIỀN khi buồn? #shorts 2024") == ["vì", "sao", "tiêu", "tiền", "khi", "buồn"]
    assert word_count("tiêu tiền khi buồn", "vi") == 4
    assert word_count("朝の祈り 祈り", "ja") == 6
    assert contains_all("Tâm lý học về TIỀN cho người mới", "tiền tâm lý", "vi")


def test_repeated_phrases_needs_two_channels_and_drops_topic_words():
    videos = [
        {"video_id": "1", "channel_id": "A", "title": "Tâm lý học về tiền: vì sao tiêu khi buồn"},
        {"video_id": "2", "channel_id": "B", "title": "Tâm lý học về tiền cho người mới đi làm"},
        {"video_id": "3", "channel_id": "B", "title": "Bẫy tâm lý khi mua sắm online"},
        {"video_id": "4", "channel_id": "C", "title": "Tâm lý học tội phạm"},
    ]
    phrases = [p["phrase"] for p in sn.repeated_phrases(videos, "tâm lý học")]
    assert "tâm lý học về tiền" in phrases
    assert "tâm lý học" not in phrases  # chỉ gồm từ của chủ đề gốc
    assert "học về" not in phrases  # cụm con bị cụm dài hơn phủ
    assert "mua sắm online" not in phrases  # chỉ 1 kênh


def test_repeated_phrases_on_real_fixture(raw, rpm_tmp):
    r = run_svc._analyze(raw, "Lịch sử", [])
    phrases = sn.repeated_phrases(r["outliers"], raw["params"]["topic"])
    assert phrases and all(p["channels"] >= 2 for p in phrases)
    assert not any(p["phrase"] in ("kể chuyện", "lịch sử", "chuyện lịch sử") for p in phrases)


def test_keyword_kind_three_columns():
    assert sn.keyword_kind("tâm lý học về tiền", "tâm lý học", "vi") == sn.SUBNICHE
    assert sn.keyword_kind("tiêu tiền khi buồn", "tâm lý học", "vi") == sn.LONG
    assert sn.keyword_kind("tâm lý", "tâm lý học", "vi") == sn.SHORT
    assert sn.keyword_kind("朝の祈り 短い", "朝の祈り", "ja") == sn.SUBNICHE
    # Từ gốc đã là ngách con tầng 2 (đủ dài): gợi ý mở rộng là từ khoá dài để chấm.
    assert sn.keyword_kind("podcast tâm lý học về tiền", "tâm lý học về tiền", "vi") == sn.LONG


def test_columns_rank_subniches_by_evidence():
    sugg = [
        {"keyword": "tâm lý học", "rank": 1, "source": "suggest"},
        {"keyword": "tâm lý học tội phạm", "rank": 2, "source": "suggest"},
        {"keyword": "tâm lý học về tiền", "rank": 3, "source": "suggest"},
    ]
    winners = [{"title": "Tâm lý học về tiền tập 1", "channel_id": "A"}, {"title": "tâm lý học về tiền 2", "channel_id": "B"}]
    cols = sn.columns(sugg, "tâm lý học", "vi", winners)
    assert [x["keyword"] for x in cols[sn.SUBNICHE]] == ["tâm lý học về tiền", "tâm lý học tội phạm"]
    assert cols[sn.SUBNICHE][0]["videos"] == 2 and cols[sn.SUBNICHE][0]["channels"] == 2


# ---------------------------------------------------------------------------
# Tầng và "đủ sống"
# ---------------------------------------------------------------------------


def test_layer_status_rules():
    assert sn.layer_status(_run())["status"] == "alive"
    # Câu hỏi kiểm tra bài 2.3: "Mẫu còn ít", không có video vượt trội → quá hẹp, lùi lên một tầng.
    narrow = sn.layer_status(_run({"confidence": "thấp", "outlier_counts": {"total": 0}}))
    assert narrow["status"] == "too_narrow" and "lùi lên một tầng" in narrow["text"]
    assert sn.layer_status(_run({"small_boom_channels": []}))["status"] == "weak"
    assert sn.layer_status(_run(layer=4))["status"] == "video"


def test_ideas_parsing_and_duplicates():
    text = "1. Vì sao tiêu tiền khi buồn\n- vì sao tiêu tiền khi buồn?\n\n2) Bẫy giảm giá 50%\n• Tiết kiệm khi lương thấp"
    ideas = sn.parse_ideas(text)
    assert ideas[0] == "Vì sao tiêu tiền khi buồn" and len(ideas) == 4
    assert sn.duplicate_ideas(ideas) == [(0, 1)]
    assert sn.unique_ideas(ideas) == 3


def test_viability_five_checks():
    words = (
        "lương thưởng nợ thẻ vay mua sắm online giảm giá tiết kiệm đầu tư vàng bạc nhà xe cưới con cái bố mẹ "
        "bạn bè hẹn hò quà sách game"
    ).split()
    ideas = "\n".join(f"Bẫy tâm lý {w}" for w in words)
    v = sn.viability(_run(), ideas, "Kênh giúp người mới đi làm hiểu vì sao mình tiêu tiền như vậy.")
    assert v["passed"] and v["unique_ideas"] == 30

    v = sn.viability(_run({"rpm_tier": None}), "", "Câu một. Câu hai.")
    by = {c["key"]: c for c in v["checks"]}
    assert by["ideas"]["ok"] is None and by["promise"]["ok"] is False and by["money"]["ok"] is None
    assert not v["passed"]

    v = sn.viability(_run({"rpm_tier": "thấp"}, affiliates=["sách"]), "", "")
    assert {c["key"]: c for c in v["checks"]}["money"]["ok"] is True  # RPM thấp nhưng còn affiliate


# ---------------------------------------------------------------------------
# Điểm từ khoá dài
# ---------------------------------------------------------------------------


def _results(n, views, subs, title="khác"):
    return [{"title": title, "views": views, "subs": subs, "channel_id": f"c{i}"} for i in range(n)]


def test_keyword_score_demand_and_competition():
    easy = ks.score_keyword("tiêu tiền khi buồn", _results(30, 100_000, 5_000), "vi")
    assert easy["demand"] == 100 and easy["competition"] == 0 and easy["score"] == 100 and easy["label"] == ks.VERY_GOOD

    crowded = ks.score_keyword("tiêu tiền khi buồn", _results(30, 100_000, 500_000, "Tiêu tiền khi buồn"), "vi")
    assert crowded["competition"] == 100 and crowded["score"] == 50 and crowded["label"] == ks.AVERAGE

    thin = ks.score_keyword("x y z", _results(5, 1000, None), "vi")
    assert thin["low_data"] and thin["big_share"] == 0


def test_keyword_labels_thresholds():
    assert ks.label(70) == ks.VERY_GOOD and ks.label(55) == ks.GOOD and ks.label(54.9) == ks.AVERAGE
    assert ks.label(40) == ks.AVERAGE and ks.label(39) == ks.WEAK and ks.label(None) is None


def test_long_keyword_scorecard_suggestion():
    good = [{"score": 60, "low_data": False}] * 10
    assert ks.long_keyword_score(good)[0] == 5
    assert ks.long_keyword_score([{"score": 80, "low_data": True}] * 10)[0] == 1  # ít dữ liệu không tính
    assert ks.long_keyword_score([])[0] is None

    run = {"id": 7, "result": {"median_views": 1000, "trends": {}, "small_boom_channels": [], "competition": 10},
           "affiliates": [], "category": ""}
    s = sc.auto_suggestions([run], {7: good[:4]})
    assert s[7]["long_keywords"][0] == 3


# ---------------------------------------------------------------------------
# Nguồn gợi ý + service từ khoá
# ---------------------------------------------------------------------------


def _suggest_transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.url.params["q"]
        calls.append(q)
        items = [q, f"{q} về tiền", f"{q} tội phạm"] if q == "tâm lý học" else [f"{q}dler", "tâm lý học về tiền"]
        return httpx.Response(200, text=json.dumps([q, items, [], {}], ensure_ascii=False))

    return httpx.MockTransport(handler)


def test_suggestions_cache_and_az(conn):
    calls = []
    out = asyncio.run(suggest.suggestions(conn, "tâm lý học", "VN", "vi", transport=_suggest_transport(calls)))
    assert [x["keyword"] for x in out] == ["tâm lý học", "tâm lý học về tiền", "tâm lý học tội phạm"]
    asyncio.run(suggest.suggestions(conn, "tâm lý học", "VN", "vi", transport=_suggest_transport(calls)))
    assert len(calls) == 1  # lần 2 đọc cache

    out = asyncio.run(suggest.suggestions(conn, "tâm lý học", "VN", "vi", az=True, transport=_suggest_transport(calls)))
    assert len(calls) == 1 + len(suggest.AZ_LETTERS)
    assert "f" not in suggest.AZ_LETTERS
    keywords = [x["keyword"] for x in out]
    assert len(keywords) == len(set(keywords)) and out[0]["source"] == "suggest"


def test_keyword_service_flow(conn, monkeypatch):
    async def fake_suggestions(conn_, seed, region, language, az=False):
        return [{"keyword": seed, "source": "suggest", "rank": 1}, {"keyword": "tiêu tiền khi buồn", "source": "suggest", "rank": 2}]

    async def fake_score_many(conn_, region, keywords, fresh):
        return [ks.score_keyword(k, _results(30, 50_000, 1_000), "vi") for k in keywords], 102 * len(keywords)

    monkeypatch.setattr(kw_svc, "suggestions", fake_suggestions)
    monkeypatch.setattr(kw_svc, "_score_many", fake_score_many)

    with pytest.raises(kw_svc.KeywordError):
        kw_svc.fetch_suggestions(conn, "  ", "VN")
    kw_svc.fetch_suggestions(conn, "Tâm  lý học về tiền", "VN")
    kw_svc.add_manual(conn, "tâm lý học về tiền", "VN", ["Tiết kiệm khi lương thấp", ""])
    assert len(kw_svc.list_keywords(conn, "tâm lý học về tiền", "VN")) == 3

    with pytest.raises(kw_svc.KeywordError):
        kw_svc.score(conn, "VN", [f"k{i}" for i in range(11)])
    assert kw_svc.score(conn, "VN", ["tiêu tiền khi buồn"]) == 102
    scored = kw_svc.scored_for(conn, "tâm lý học về tiền", "VN")
    assert [k["keyword"] for k in scored] == ["tiêu tiền khi buồn"] and scored[0]["low_data"] is False

    # Lấy gợi ý lại không xoá điểm đã chấm.
    kw_svc.fetch_suggestions(conn, "tâm lý học về tiền", "VN")
    assert kw_svc.scored_for(conn, "tâm lý học về tiền", "VN")[0]["score"] == scored[0]["score"]

    kw_svc.set_picked(conn, "VN", ["tiêu tiền khi buồn"], True)
    assert [k["keyword"] for k in kw_svc.list_keywords(conn, region="VN") if k["picked"]] == ["tiêu tiền khi buồn"]
    assert kw_svc.seeds(conn)[0]["scored"] == 1


# ---------------------------------------------------------------------------
# Đào ngách con: tầng, cây, migration
# ---------------------------------------------------------------------------


def test_dig_child_sets_layer_and_tree(conn, raw, rpm_tmp, monkeypatch):
    async def fake_fetch(conn_, params, fresh=False):
        return {**raw, "params": {**raw["params"], "topic": params.topic}}

    monkeypatch.setattr(run_svc, "fetch_niche", fake_fetch)
    root = run_svc.dig(conn, DigParams(topic="lịch sử"), category="Lịch sử")
    child = run_svc.dig(conn, DigParams(topic="lịch sử nhật thời edo"), category="Lịch sử", parent_id=root, filter_type="angle")
    grand = run_svc.dig(conn, DigParams(topic="samurai thời edo"), category="Lịch sử", parent_id=child, filter_type="audience")

    assert run_repo.get(conn, grand)["layer"] == 3
    again = run_svc.redig(conn, child)
    assert run_repo.get(conn, again)["parent_id"] == root and run_repo.get(conn, again)["layer"] == 2

    nodes = sub_svc.tree(conn, root)
    assert [n["run"]["id"] for n in nodes] == [root, child, grand, again]
    assert [n["depth"] for n in nodes] == [0, 1, 2, 1]
    assert sub_svc.root_of(conn, grand) == root

    sub_svc.save_check(conn, child, ideas="a\nb", promise="  Kênh   giúp bạn hiểu Edo. ")
    c = sub_svc.check(conn, child)
    assert c["promise"] == "Kênh giúp bạn hiểu Edo." and c["layer"]["status"] in ("alive", "weak")
    run_repo.delete(conn, child)
    assert conn.execute("SELECT COUNT(*) FROM niche_check").fetchone()[0] == 0


def test_migration_adds_columns_to_old_db(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        """CREATE TABLE dig_run (id INTEGER PRIMARY KEY, topic TEXT NOT NULL, region TEXT NOT NULL, language TEXT NOT NULL,
           window_days INTEGER NOT NULL, fmt TEXT NOT NULL, category TEXT, created_at TEXT NOT NULL, score REAL, label TEXT,
           confidence TEXT, verdict TEXT, affiliates TEXT NOT NULL DEFAULT '[]', checked_channels TEXT NOT NULL DEFAULT '[]',
           shortlisted INTEGER NOT NULL DEFAULT 0, raw_json TEXT NOT NULL, result_json TEXT NOT NULL)"""
    )
    old.execute(
        "INSERT INTO dig_run (topic, region, language, window_days, fmt, created_at, raw_json, result_json) "
        "VALUES ('x', 'VN', 'vi', 30, 'any', '2026-10-01', '{}', '{}')"
    )
    old.commit()
    old.close()
    conn = connect(path)
    row = conn.execute("SELECT layer, parent_id, filter_type FROM dig_run").fetchone()
    assert (row["layer"], row["parent_id"], row["filter_type"]) == (1, None, "")
    connect(path).close()  # chạy lại không lỗi


# ---------------------------------------------------------------------------
# Prompt bài 2.3
# ---------------------------------------------------------------------------


def test_lesson_23_prompts(conn, raw, rpm_tmp):
    rid = run_repo.insert(conn, raw=raw, result=run_svc._analyze(raw, "Lịch sử", []), category="Lịch sử", affiliates=[])
    run = run_repo.get(conn, rid)
    cols = sn.columns([{"keyword": "kể chuyện lịch sử việt nam", "rank": 1, "source": "suggest"}], run["topic"], "vi", [])
    p1 = report.build_subniche_prompt(run, cols, sn.repeated_phrases(run["result"]["outliers"], run["topic"]), "không lộ mặt")
    assert "kể chuyện lịch sử việt nam" in p1 and "MỘT bộ lọc" in p1 and "{" not in p1
    p2 = report.build_ideas_prompt(run, "Kênh giúp bạn hiểu sử Việt.", [{"keyword": "a b c d", "score": 61.0, "low_data": True}])
    assert "a b c d: 61 (ít dữ liệu)" in p2 and "30 ý tưởng" in p2 and "{" not in p2


def test_keyword_belongs_to_many_seeds_and_shares_score(conn, monkeypatch):
    async def fake_suggestions(conn_, seed, region, language, az=False):
        return [{"keyword": "tâm lý học về tiền", "source": "suggest", "rank": 1}]

    async def fake_score_many(conn_, region, keywords, fresh):
        return [ks.score_keyword(k, _results(30, 50_000, 1_000), "vi") for k in keywords], 102

    monkeypatch.setattr(kw_svc, "suggestions", fake_suggestions)
    monkeypatch.setattr(kw_svc, "_score_many", fake_score_many)
    kw_svc.fetch_suggestions(conn, "tâm lý học về tiền", "VN")
    kw_svc.score(conn, "VN", ["tâm lý học về tiền"])
    kw_svc.fetch_suggestions(conn, "tâm lý học", "VN")
    shared = kw_svc.list_keywords(conn, "tâm lý học", "VN")
    assert [k["keyword"] for k in shared] == ["tâm lý học về tiền"] and shared[0]["score"] is not None


def test_old_keyword_table_is_rebuilt(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE keyword (region TEXT, keyword TEXT, seed TEXT, PRIMARY KEY (region, keyword))")
    old.commit()
    old.close()
    conn = connect(path)
    pk = [r["name"] for r in sorted(conn.execute("PRAGMA table_info(keyword)"), key=lambda r: r["pk"]) if r["pk"]]
    assert pk == ["region", "seed", "keyword"]
