import json

import pytest

from yt_niche.core.confidence import HIGH
from yt_niche.core.niche_score import LABEL_GREEN
from yt_niche.core.three_tier import TRY
from yt_niche.core.trends_classify import STABLE
from yt_niche.services import niche_map as nm


def _insert_run(conn, topic="tâm lý học", region="VN", score=72.0, median_views=50000, booms=2, trend=STABLE):
    result = {
        "score": score, "median_views": median_views, "labels": [LABEL_GREEN], "confidence": HIGH,
        "three_tier": {"verdict": TRY}, "small_boom_channels": [{}] * booms,
        "outlier_counts": {"total": 5}, "trends": {"class": trend},
    }
    return conn.execute(
        """INSERT INTO dig_run (topic, region, language, window_days, fmt, created_at, score, label, confidence, verdict,
               affiliates, raw_json, result_json) VALUES (?, ?, 'vi', 30, 'any', '2026-11-01T00:00:00+00:00', ?, 'x', ?,
               ?, '[]', '{}', ?)""",
        (topic, region, score, HIGH, TRY, json.dumps(result)),
    ).lastrowid


def _insert_keyword(conn, kw, region="VN", score=60.0, demand=55.0, competition=30.0):
    conn.execute(
        """INSERT INTO keyword (region, keyword, seed, language, source, created_at, scored_at, score, demand, competition, n)
           VALUES (?, ?, 'seed', 'vi', 'suggest', '2026-11-01T00:00:00', '2026-11-01T00:00:00', ?, ?, ?, 40)""",
        (region, kw, score, demand, competition),
    )


def test_create_requires_valid_motivation(conn):
    with pytest.raises(nm.NicheError):
        nm.create(conn, "Psychology", language="vi", motivation="fun")
    with pytest.raises(nm.NicheError):
        nm.create(conn, "Psychology", language="xx")
    nid = nm.create(conn, "Applied psychology", language="vi", motivation="learn", motivation_secondary="improve", rationale="test creation")
    d = nm.get(conn, nid)
    assert d["status"] == "draft" and d["motivation"] == "learn"
    assert d["events"][0]["kind"] == "create"


def test_attach_keywords_copies_numbers_and_activates(conn):
    _insert_keyword(conn, "hiệu ứng tâm lý", score=70, demand=65, competition=20)
    nid = nm.create(conn, "Psychological effects", language="vi", motivation="learn")
    d = nm.attach_keywords(
        conn, nid,
        [{"keyword": "Hiệu Ứng Tâm Lý", "region": "vn", "kind": "main"}, {"keyword": "tâm lý học về tiền", "region": "VN", "kind": "longtail"}],
        rationale="main keyword has real numbers, long-tail not scored yet",
    )
    assert d["status"] == "active"
    main = d["keywords"]["main"][0]
    assert (main["keyword"], main["region"], main["demand"]) == ("hiệu ứng tâm lý", "VN", 65)
    assert d["keywords"]["longtail"][0]["score"] is None
    assert d["signals"]["main_demand"] == 65 and d["signals"]["n_main_scored"] == 1
    with pytest.raises(nm.NicheError):
        nm.attach_keywords(conn, nid, [{"keyword": "x", "region": "VN", "kind": "weird"}], rationale="a long enough rationale")
    with pytest.raises(nm.NicheError):
        nm.attach_keywords(conn, nid, [{"keyword": "x", "region": "VN", "kind": "main"}], rationale="short")


def test_attach_run_signals_and_rank_stale(conn):
    nid = nm.create(conn, "Psychology", language="vi", motivation="learn")
    run1 = _insert_run(conn, score=72)
    nm.attach_run(conn, nid, run1)
    sig = nm.signals(conn, nid)
    assert sig["n_runs"] == 1 and sig["best_run_score"] == 72 and sig["trend_class"] == STABLE
    nm.rank(conn, [{"niche_id": nid, "potential": 80, "rationale": "high dig score, evergreen trend, 2 small booming channels"}])
    d = nm.get(conn, nid)
    assert d["potential"] == 80 and d["stale"] is False and d["basis"]["n_runs"] == 1
    run2 = _insert_run(conn, score=40)
    nm.attach_run(conn, nid, run2)
    assert nm.get(conn, nid)["stale"] is True
    table = nm.compare_table(conn)
    assert table[0]["id"] == nid and table[0]["stale"] is True and table[0]["n_runs"] == 2
    with pytest.raises(nm.NicheError):
        nm.rank(conn, [{"niche_id": nid, "potential": 120, "rationale": "too high, must be rejected"}])


def test_tree_move_and_cycle_guard(conn):
    root = nm.create(conn, "Psychology", language="vi", motivation="learn")
    child = nm.create(conn, "Money psychology", language="vi", parent_id=root, motivation="improve")
    grand = nm.create(conn, "Spending when sad", language="vi", parent_id=child, motivation="fix")
    tree = nm.list_tree(conn)
    assert [(t["name"], t["depth"]) for t in tree] == [("Psychology", 0), ("Money psychology", 1), ("Spending when sad", 2)]
    assert tree[2]["path"] == "Psychology / Money psychology / Spending when sad"
    with pytest.raises(nm.NicheError):
        nm.move(conn, root, grand, rationale="must not create a cycle")
    nm.move(conn, grand, root, rationale="move up to the same layer as money")
    assert nm.get(conn, grand)["parent_id"] == root


def test_merge_moves_data_and_unmerge_restores(conn):
    _insert_keyword(conn, "a", score=50)
    a = nm.create(conn, "Niche A", language="vi", motivation="learn")
    b = nm.create(conn, "Niche B", language="vi", motivation="learn")
    nm.attach_keywords(conn, a, [{"keyword": "a", "region": "VN", "kind": "main"}, {"keyword": "shared", "region": "VN", "kind": "related"}],
                       rationale="attach keywords to A")
    nm.attach_keywords(conn, b, [{"keyword": "shared", "region": "VN", "kind": "related"}], rationale="attach keywords to B")
    run = _insert_run(conn)
    nm.attach_run(conn, a, run)
    nm.set_channels(conn, a, [{"channel_id": "UC1", "role": "rising"}], rationale="small booming channel")
    child = nm.create(conn, "Child of A", language="vi", parent_id=a)

    merged = nm.merge(conn, a, b, rationale="the two niches share 60% of main keywords and the motivation")
    assert merged["id"] == b and merged["status"] == "active"
    assert {k["keyword"] for k in merged["keywords"]["main"] + merged["keywords"]["related"]} == {"a", "shared"}
    assert [r["id"] for r in merged["runs"]] == [run]
    assert merged["channels"][0]["channel_id"] == "UC1"
    assert nm.get(conn, child)["parent_id"] == b
    src = nm.get(conn, a)
    assert src["status"] == "merged" and src["merged_into"] == b and src["keywords"]["main"] == []
    assert all(t["id"] != a for t in nm.list_tree(conn))
    assert any(t["id"] == a for t in nm.list_tree(conn, include_merged=True))
    with pytest.raises(nm.NicheError):
        nm.merge(conn, a, b, rationale="merging again must be blocked")

    back = nm.unmerge(conn, a, rationale="merged by mistake, split again")
    assert back["status"] == "active" and back["merged_into"] is None
    assert {k["keyword"] for k in back["keywords"]["main"]} == {"a"}
    assert [r["id"] for r in back["runs"]] == [run] and nm.get(conn, b)["runs"] == []
    assert nm.get(conn, child)["parent_id"] == a
    # The duplicate keyword ("shared") stays on B, it is not pulled back.
    assert {k["keyword"] for k in nm.get(conn, b)["keywords"]["related"]} == {"shared"}


def test_find_similar_and_refresh_numbers(conn):
    _insert_keyword(conn, "hiệu ứng tâm lý", score=70, demand=65)
    nid = nm.create(conn, "Psychological effects", language="vi", motivation="learn")
    nm.attach_keywords(conn, nid, [{"keyword": "hiệu ứng tâm lý", "region": "VN", "kind": "main"}], rationale="main keyword")
    hits = nm.find_similar_niches(conn, [{"keyword": "Hiệu ứng tâm lý", "region": "vn"}, {"keyword": "other", "region": "VN"}])
    assert hits == [{"id": nid, "name": "Psychological effects", "language": "vi", "motivation": "learn", "overlap": ["hiệu ứng tâm lý"]}]
    conn.execute("UPDATE keyword SET demand = 90, scored_at = '2026-11-03T00:00:00' WHERE keyword = 'hiệu ứng tâm lý'")
    assert nm.refresh_keyword_numbers(conn, nid) == 1
    assert nm.get(conn, nid)["keywords"]["main"][0]["demand"] == 90
    assert nm.refresh_keyword_numbers(conn, nid) == 0


def test_motivation_change_and_drop_logged(conn):
    nid = nm.create(conn, "X", language="vi", motivation="learn")
    nm.set_motivation(conn, nid, "earn", "learn", rationale="the winning keywords are all about making money")
    with pytest.raises(nm.NicheError):
        nm.set_motivation(conn, nid, "earn", "earn", rationale="secondary equal to primary must be blocked")
    nm.drop(conn, nid, rationale="competition is all big channels, drop")
    d = nm.get(conn, nid)
    assert d["status"] == "dropped" and [e["kind"] for e in d["events"][:2]] == ["drop", "motivation"]
    assert d["events"][1]["payload"]["to"] == ["earn", "learn"]


def test_one_language_per_niche(conn):
    vi = nm.create(conn, "Tâm lý học", language="vi", motivation="learn")
    en = nm.create(conn, "Psychology", language="en", motivation="learn")
    assert nm.get(conn, en)["regions_allowed"] == ["US", "GB", "CA", "AU", "PH"]
    # A US-market keyword cannot be attached to a Vietnamese niche.
    with pytest.raises(nm.NicheError, match="one language"):
        nm.attach_keywords(conn, vi, [{"keyword": "psychology facts", "region": "US", "kind": "main"}], rationale="wrong language")
    # Same language, different market (GB vs US) is fine.
    nm.attach_keywords(conn, en, [{"keyword": "psychology facts", "region": "GB", "kind": "related"}], rationale="both English")
    # An English dig cannot be attached to a Vietnamese niche.
    run_en = conn.execute(
        """INSERT INTO dig_run (topic, region, language, window_days, fmt, created_at, score, label, confidence, verdict,
               affiliates, raw_json, result_json) VALUES ('psychology', 'US', 'en', 30, 'any', '2026-11-01T00:00:00+00:00',
               50, 'x', ?, ?, '[]', '{}', '{}')""",
        (HIGH, TRY),
    ).lastrowid
    with pytest.raises(nm.NicheError, match="one language"):
        nm.attach_run(conn, vi, run_en)
    nm.attach_run(conn, en, run_en)
    # No merging, no nesting across languages.
    with pytest.raises(nm.NicheError):
        nm.merge(conn, vi, en, rationale="different languages must be blocked")
    with pytest.raises(nm.NicheError):
        nm.move(conn, vi, en, rationale="different languages must be blocked")
    with pytest.raises(nm.NicheError):
        nm.create(conn, "Child", language="en", parent_id=vi)
    assert [t["language"] for t in nm.list_tree(conn)] == ["en", "vi"] or [t["language"] for t in nm.list_tree(conn)] == ["vi", "en"]
