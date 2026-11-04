import json

import pytest

from yt_niche.services import niche_map as nm


def _insert_run(conn, topic="tâm lý học", region="VN", score=72.0, median_views=50000, booms=2, trend="stable"):
    result = {
        "score": score, "median_views": median_views, "labels": ["Ngách xanh"], "confidence": "Cao",
        "three_tier": {"verdict": "THỬ"}, "small_boom_channels": [{}] * booms,
        "outlier_counts": {"total": 5}, "trends": {"class": trend},
    }
    return conn.execute(
        """INSERT INTO dig_run (topic, region, language, window_days, fmt, created_at, score, label, confidence, verdict,
               affiliates, raw_json, result_json) VALUES (?, ?, 'vi', 30, 'any', '2026-11-01T00:00:00+00:00', ?, 'x', 'Cao',
               'THỬ', '[]', '{}', ?)""",
        (topic, region, score, json.dumps(result)),
    ).lastrowid


def _insert_keyword(conn, kw, region="VN", score=60.0, demand=55.0, competition=30.0):
    conn.execute(
        """INSERT INTO keyword (region, keyword, seed, language, source, created_at, scored_at, score, demand, competition, n)
           VALUES (?, ?, 'seed', 'vi', 'suggest', '2026-11-01T00:00:00', '2026-11-01T00:00:00', ?, ?, ?, 40)""",
        (region, kw, score, demand, competition),
    )


def test_create_requires_valid_motivation(conn):
    with pytest.raises(nm.NicheError):
        nm.create(conn, "Tâm lý", motivation="fun")
    nid = nm.create(conn, "Tâm lý ứng dụng", motivation="learn", motivation_secondary="improve", rationale="tạo thử")
    d = nm.get(conn, nid)
    assert d["status"] == "draft" and d["motivation"] == "learn"
    assert d["events"][0]["kind"] == "create"


def test_attach_keywords_copies_numbers_and_activates(conn):
    _insert_keyword(conn, "hiệu ứng tâm lý", score=70, demand=65, competition=20)
    nid = nm.create(conn, "Hiệu ứng tâm lý", motivation="learn")
    d = nm.attach_keywords(
        conn, nid,
        [{"keyword": "Hiệu Ứng Tâm Lý", "region": "vn", "kind": "main"}, {"keyword": "tâm lý học về tiền", "region": "VN", "kind": "longtail"}],
        rationale="từ khoá chính có số thật, đuôi dài chưa chấm",
    )
    assert d["status"] == "active"
    main = d["keywords"]["main"][0]
    assert (main["keyword"], main["region"], main["demand"]) == ("hiệu ứng tâm lý", "VN", 65)
    assert d["keywords"]["longtail"][0]["score"] is None
    assert d["signals"]["main_demand"] == 65 and d["signals"]["n_main_scored"] == 1
    with pytest.raises(nm.NicheError):
        nm.attach_keywords(conn, nid, [{"keyword": "x", "region": "VN", "kind": "weird"}], rationale="lý do đủ dài")
    with pytest.raises(nm.NicheError):
        nm.attach_keywords(conn, nid, [{"keyword": "x", "region": "VN", "kind": "main"}], rationale="ngắn")


def test_attach_run_signals_and_rank_stale(conn):
    nid = nm.create(conn, "Tâm lý học", motivation="learn")
    run1 = _insert_run(conn, score=72)
    nm.attach_run(conn, nid, run1)
    sig = nm.signals(conn, nid)
    assert sig["n_runs"] == 1 and sig["best_run_score"] == 72 and sig["trend_class"] == "stable"
    nm.rank(conn, [{"niche_id": nid, "potential": 80, "rationale": "điểm đào cao, trend bền, 2 kênh nhỏ nổ"}])
    d = nm.get(conn, nid)
    assert d["potential"] == 80 and d["stale"] is False and d["basis"]["n_runs"] == 1
    run2 = _insert_run(conn, score=40)
    nm.attach_run(conn, nid, run2)
    assert nm.get(conn, nid)["stale"] is True
    table = nm.compare_table(conn)
    assert table[0]["id"] == nid and table[0]["stale"] is True and table[0]["n_runs"] == 2
    with pytest.raises(nm.NicheError):
        nm.rank(conn, [{"niche_id": nid, "potential": 120, "rationale": "quá cao không hợp lệ"}])


def test_tree_move_and_cycle_guard(conn):
    root = nm.create(conn, "Tâm lý", motivation="learn")
    child = nm.create(conn, "Tâm lý tiền bạc", parent_id=root, motivation="improve")
    grand = nm.create(conn, "Tiêu tiền khi buồn", parent_id=child, motivation="fix")
    tree = nm.list_tree(conn)
    assert [(t["name"], t["depth"]) for t in tree] == [("Tâm lý", 0), ("Tâm lý tiền bạc", 1), ("Tiêu tiền khi buồn", 2)]
    assert tree[2]["path"] == "Tâm lý / Tâm lý tiền bạc / Tiêu tiền khi buồn"
    with pytest.raises(nm.NicheError):
        nm.move(conn, root, grand, rationale="không được tạo vòng")
    nm.move(conn, grand, root, rationale="đưa lên cùng tầng với tiền bạc")
    assert nm.get(conn, grand)["parent_id"] == root


def test_merge_moves_data_and_unmerge_restores(conn):
    _insert_keyword(conn, "a", score=50)
    a = nm.create(conn, "Ngách A", motivation="learn")
    b = nm.create(conn, "Ngách B", motivation="learn")
    nm.attach_keywords(conn, a, [{"keyword": "a", "region": "VN", "kind": "main"}, {"keyword": "chung", "region": "VN", "kind": "related"}],
                       rationale="gắn từ khoá cho A")
    nm.attach_keywords(conn, b, [{"keyword": "chung", "region": "VN", "kind": "related"}], rationale="gắn từ khoá cho B")
    run = _insert_run(conn)
    nm.attach_run(conn, a, run)
    nm.set_channels(conn, a, [{"channel_id": "UC1", "role": "rising"}], rationale="kênh nhỏ đang nổ")
    child = nm.create(conn, "Con của A", parent_id=a)

    merged = nm.merge(conn, a, b, rationale="hai ngách trùng 60% từ khoá chính, cùng động cơ")
    assert merged["id"] == b and merged["status"] == "active"
    assert {k["keyword"] for k in merged["keywords"]["main"] + merged["keywords"]["related"]} == {"a", "chung"}
    assert [r["id"] for r in merged["runs"]] == [run]
    assert merged["channels"][0]["channel_id"] == "UC1"
    assert nm.get(conn, child)["parent_id"] == b
    src = nm.get(conn, a)
    assert src["status"] == "merged" and src["merged_into"] == b and src["keywords"]["main"] == []
    assert all(t["id"] != a for t in nm.list_tree(conn))
    assert any(t["id"] == a for t in nm.list_tree(conn, include_merged=True))
    with pytest.raises(nm.NicheError):
        nm.merge(conn, a, b, rationale="gộp lần nữa phải bị chặn")

    back = nm.unmerge(conn, a, rationale="gộp nhầm, tách lại")
    assert back["status"] == "active" and back["merged_into"] is None
    assert {k["keyword"] for k in back["keywords"]["main"]} == {"a"}
    assert [r["id"] for r in back["runs"]] == [run] and nm.get(conn, b)["runs"] == []
    assert nm.get(conn, child)["parent_id"] == a
    # Từ khoá trùng ("chung") ở lại B, không bị kéo về.
    assert {k["keyword"] for k in nm.get(conn, b)["keywords"]["related"]} == {"chung"}


def test_find_similar_and_refresh_numbers(conn):
    _insert_keyword(conn, "hiệu ứng tâm lý", score=70, demand=65)
    nid = nm.create(conn, "Hiệu ứng tâm lý", motivation="learn")
    nm.attach_keywords(conn, nid, [{"keyword": "hiệu ứng tâm lý", "region": "VN", "kind": "main"}], rationale="từ khoá chính")
    hits = nm.find_similar_niches(conn, [{"keyword": "Hiệu ứng tâm lý", "region": "vn"}, {"keyword": "khác", "region": "VN"}])
    assert hits == [{"id": nid, "name": "Hiệu ứng tâm lý", "motivation": "learn", "overlap": ["hiệu ứng tâm lý"]}]
    conn.execute("UPDATE keyword SET demand = 90, scored_at = '2026-11-03T00:00:00' WHERE keyword = 'hiệu ứng tâm lý'")
    assert nm.refresh_keyword_numbers(conn, nid) == 1
    assert nm.get(conn, nid)["keywords"]["main"][0]["demand"] == 90
    assert nm.refresh_keyword_numbers(conn, nid) == 0


def test_motivation_change_and_drop_logged(conn):
    nid = nm.create(conn, "X", motivation="learn")
    nm.set_motivation(conn, nid, "earn", "learn", rationale="từ khoá thắng đều về kiếm tiền")
    with pytest.raises(nm.NicheError):
        nm.set_motivation(conn, nid, "earn", "earn", rationale="phụ trùng chính phải bị chặn")
    nm.drop(conn, nid, rationale="cạnh tranh toàn kênh lớn, bỏ")
    d = nm.get(conn, nid)
    assert d["status"] == "dropped" and [e["kind"] for e in d["events"][:2]] == ["drop", "motivation"]
    assert d["events"][1]["payload"]["to"] == ["earn", "learn"]
