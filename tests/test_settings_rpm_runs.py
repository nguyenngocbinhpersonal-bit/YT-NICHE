import json
from pathlib import Path

import pytest

from yt_niche import config as cfg
from yt_niche import settings
from yt_niche.db import runs as run_repo
from yt_niche.services import report
from yt_niche.services.runs import _analyze, compare, reanalyze
from yt_niche.sources import rpm as rpm_source

FIXTURE = Path(__file__).parent / "fixtures" / "history_vn_any.json"


@pytest.fixture
def tmp_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(cfg, "RPM_TABLE_PATH", tmp_path / "rpm.csv")
    yield tmp_path
    settings.apply({})  # restore the default config for the other tests


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_settings_save_apply_and_reset(tmp_paths):
    settings.save({"OUTLIER_OUTPERFORM": 4, "MIN_CHANNEL_SAMPLE": 5})
    assert cfg.OUTLIER_OUTPERFORM == 4.0
    assert json.loads(cfg.SETTINGS_PATH.read_text()) == {"OUTLIER_OUTPERFORM": 4}  # only values that differ from defaults

    settings.apply({})
    assert cfg.OUTLIER_OUTPERFORM == settings.DEFAULTS["OUTLIER_OUTPERFORM"]
    settings.init()
    assert cfg.OUTLIER_OUTPERFORM == 4.0


def test_settings_validate():
    assert settings.validate({"NICHE_WEIGHTS": {"demand": 0.5, "opportunity": 0.5, "freshness": 0.2, "competition": 0}})
    assert settings.validate({"DEMAND_LOG_RANGE": (6, 3)})
    assert settings.validate({"OUTLIER_VIRAL": 2})
    assert settings.validate({"NICHE_WEIGHTS": dict(settings.DEFAULTS["NICHE_WEIGHTS"])}) == []


def test_threshold_change_affects_analysis(tmp_paths, raw):
    base = _analyze(raw, None, [])
    settings.save({"OUTLIER_OUTPERFORM": 9})
    stricter = _analyze(raw, None, [])
    assert stricter["outlier_counts"]["total"] < base["outlier_counts"]["total"]


def test_rpm_table_default_and_lookup(tmp_paths):
    assert not cfg.RPM_TABLE_PATH.exists()
    vn = rpm_source.lookup("VN", "History")
    us = rpm_source.lookup("US", "History")
    assert cfg.RPM_TABLE_PATH.exists()
    assert us[0] == pytest.approx(vn[0] * 4)
    assert rpm_source.lookup("VN", None) is None
    assert rpm_source.lookup("VN", "no such category") is None


def test_rpm_table_renames_legacy_vietnamese_categories(tmp_paths):
    cfg.RPM_TABLE_PATH.write_text(
        "market,category,rpm_low,rpm_high\nVN,Lịch sử,0.9,1.9\nVN,History,0.1,0.2\nUS,Game,5,6\n", encoding="utf-8"
    )
    df = rpm_source.load_table()
    assert not df["category"].isin(rpm_source.LEGACY_CATEGORIES).any()
    assert rpm_source.lookup("VN", "History") == (0.9, 1.9)  # the user's edited row wins over the duplicate
    assert rpm_source.lookup("US", "Gaming") == (5.0, 6.0)
    assert "Lịch sử" not in cfg.RPM_TABLE_PATH.read_text(encoding="utf-8")  # rewritten in English
    assert set(df["market"]) == set(cfg.MARKETS)  # missing markets backfilled


def test_runs_insert_reanalyze_compare(conn, tmp_paths, raw):
    result = _analyze(raw, "History", [])
    run_id = run_repo.insert(conn, raw=raw, result=result, category="History", affiliates=[])
    run = run_repo.get(conn, run_id, with_raw=True)
    assert run["raw"]["params"]["topic"] == "kể chuyện lịch sử"
    assert run["result"]["three_tier"]["money"]["reason"].endswith("no affiliates entered")

    new = reanalyze(conn, run_id, category="History", affiliates=["Books", "Maps", "Stories"])
    assert "3 affiliate products" in new["three_tier"]["money"]["reason"]
    assert run_repo.get(conn, run_id)["affiliates"] == ["Books", "Maps", "Stories"]

    second = run_repo.insert(conn, raw=raw, result=result, category="History", affiliates=[])
    history = run_repo.same_setup(conn, run_repo.get(conn, second))
    assert [h["id"] for h in history] == [second, run_id]
    rows = compare(history[1], history[0])
    score_row = next(r for r in rows if "score" in str(list(r.values())[0]).lower())
    old, new_value, diff = list(score_row.values())[1:4]  # metric, old, new, diff
    assert old == new_value and diff == 0

    run_repo.set_checked_channels(conn, run_id, ["A", "B"])
    run_repo.set_shortlisted(conn, run_id, True)
    got = run_repo.get(conn, run_id)
    assert got["checked_channels"] == ["A", "B"] and got["shortlisted"] == 1

    run_repo.delete(conn, run_id)
    assert run_repo.get(conn, run_id) is None


def test_report_prompt_and_excel(conn, tmp_paths, raw):
    result = _analyze(raw, "History", ["History books"])
    run_id = run_repo.insert(conn, raw=raw, result=result, category="History", affiliates=["History books"])
    run = run_repo.get(conn, run_id)
    for name in report.PROMPTS:
        text = report.build_prompt(name, run, "faceless")
        assert "{" not in text and "kể chuyện lịch sử" in text and "faceless" in text
    assert "History books" in report.data_block(run)
    xlsx = report.to_excel(run)
    assert xlsx[:2] == b"PK"  # a zip file (xlsx)
