"""Ghi local (T5): pipectl giả bằng script stub ghi lại tham số. Không đụng folder thật, không đọc catalog thật, không gọi API thật."""

import json
import sys
from pathlib import Path

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from yt_niche import config as cfg
from yt_niche.core import upload_state as us
from yt_niche.db import uploads as repo
from yt_niche.db.connection import connect
from yt_niche.services import pipectl
from yt_niche.services import uploads as svc
from yt_niche.ui import common

PAGE = Path(__file__).resolve().parent.parent / "yt_niche" / "ui" / "pages" / "upload_page.py"
SLUG = "chan-a"
UC_A = "UC" + "a" * 22
UC_B = "UC" + "b" * 22
VID = "AAAAAAAAAAA"
VID2 = "CCCCCCCCCCC"
PUB = "2026-11-02T08:40:42Z"

STUB = r'''
import json, os, sys
a = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
if a[0] == "runs":
    print(open(os.environ["STUB_RUNS"], encoding="utf-8").read())
    sys.exit(0)
mode = os.environ.get("STUB_MODE", "ok")
rd = a[a.index("--run-dir") + 1]
fail = os.environ.get("STUB_FAIL_ON", "")
if fail and fail in rd:
    mode = os.environ.get("STUB_FAIL_MODE", "error")
if mode == "ok":
    print(json.dumps({"run_dir": rd, "project": {}, "catalog_updated": True}))
elif mode == "garbage":
    print("not json")
elif mode == "pending":
    sys.stderr.write("noise\nERROR: run marked published but the catalog was not updated (boom); re-run mark-published\n"); sys.exit(2)
elif mode == "notready":
    sys.stderr.write("ERROR: catalog not ready (down); nothing was changed, re-run mark-published\n"); sys.exit(2)
elif mode == "locked":
    sys.stderr.write("ERROR: could not rename a -> b after 5 attempts: the directory is still locked.\n"); sys.exit(1)
else:
    sys.stderr.write("Traceback (most recent call last):\n  boom\nKeyError: x\n"); sys.exit(1)
'''


def w(path: Path, data="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="")
    else:
        path.write_bytes(data)
    return path


def png(path: Path, seed: str):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (sum(map(ord, seed)) % 256, 10, 10)).save(path, format="PNG")


def pipeline(cdir: Path, name: str, vid: str, title: str):
    d = cdir / name
    w(d / "project.json", json.dumps({"video_id": vid, "channel_slug": cdir.name}))
    w(d / "artifacts" / "publish.json", json.dumps({"title": {"primary": title, "en": "EN"}, "description": {"primary": "d", "en": "d"}}))
    w(d / "final.mp4", b"\0")
    png(d / "thumbnail.jpg", title)
    return d


def run_entry(folder: Path, vid: str, state="complete"):
    return {"run_dir": str(folder), "channel_slug": folder.parent.name, "video_id": vid, "state": state,
            "lifecycle": "published" if state == "published" else "active", "created": "2026-11-01T00:00:00Z"}


def api_handler(videos: dict, log: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        ep = request.url.path.rsplit("/", 1)[-1]
        if log is not None:
            log.append(ep)
        if ep == "videos":
            items = []
            for vid in request.url.params["id"].split(","):
                v = videos.get(vid)
                if v:
                    items.append({"id": vid, "snippet": {"channelId": v["channel"], "title": v["title"], "publishedAt": v["published"]},
                                  "status": {"privacyStatus": v["privacy"]}, "contentDetails": {"duration": "PT1M"}})
            return httpx.Response(200, json={"items": items})
        return httpx.Response(500, json={"error": {"message": "unexpected"}})

    return httpx.MockTransport(handler)


def good_videos():
    return {
        VID: {"channel": UC_A, "title": "Video Mot", "privacy": "public", "published": PUB},
        VID2: {"channel": UC_A, "title": "Video Hai", "privacy": "public", "published": "2026-11-03T01:02:03Z"},
        "BBBBBBBBBBB": {"channel": UC_B, "title": "Rival", "privacy": "public", "published": PUB},
        "PPPPPPPPPPP": {"channel": UC_A, "title": "Private", "privacy": "unlisted", "published": PUB},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "social"
    cdir = root / SLUG
    d1 = pipeline(cdir, "11-01_chan-a-video-001-x", "001", "Video Mot")
    d2 = pipeline(cdir, "11-02_chan-a-video-002-y", "002", "Video Hai")
    stub = w(tmp_path / "stub.py", STUB)
    runs_file = w(tmp_path / "runs.json", json.dumps({"runs": [run_entry(d1, "001"), run_entry(d2, "002")]}))
    log = tmp_path / "calls.log"
    monkeypatch.setattr(cfg, "YT_WF_PYTHON", Path(sys.executable))
    monkeypatch.setattr(cfg, "PIPECTL_PATH", stub)
    monkeypatch.setattr(cfg, "SOCIAL_MEDIA_ROOT", root)
    monkeypatch.setattr(cfg, "YT_WF_ROOT", tmp_path / "wf")
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("STUB_RUNS", str(runs_file))
    monkeypatch.setenv("STUB_MODE", "ok")
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    db = connect(tmp_path / "app.db")
    repo.upsert_channel(db, SLUG, {"channel_id": UC_A, "handle": "@a", "title": "Real A", "uploads_playlist": "UUa", "thumb_url": ""})
    db.execute("UPDATE upload_channel SET last_refresh_at = ? WHERE channel_slug = ?", (repo.now_iso(), SLUG))

    def calls():
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]

    return {"root": root, "db": db, "d1": d1, "d2": d2, "calls": calls, "runs_file": runs_file, "tmp": tmp_path}


def confirm(env, number=1, vid=VID, videos=None, log=None):
    return svc.confirm_pipeline(env["db"], SLUG, number, vid, env["root"], api_key="k",
                                transport=api_handler(videos or good_videos(), log))


# ---------------------------------------------------------------------------
# pipectl.mark_published: các nhánh phân loại
# ---------------------------------------------------------------------------
def test_ok_passes_exact_args(env):
    r = pipectl.mark_published(env["d1"], "https://www.youtube.com/watch?v=" + VID, PUB)
    assert r.ok and r.data["catalog_updated"] is True
    a = [c for c in env["calls"]() if c[0] == "mark-published"][0]
    assert a == ["mark-published", "--run-dir", str(env["d1"]), "--url", "https://www.youtube.com/watch?v=" + VID,
                 "--published-at", PUB, "--json"]


@pytest.mark.parametrize("mode,status", [
    ("pending", pipectl.CATALOG_PENDING), ("notready", pipectl.CATALOG_NOT_READY), ("locked", pipectl.LOCKED),
    ("error", pipectl.ERROR), ("garbage", pipectl.ERROR),
])
def test_classification(env, monkeypatch, mode, status):
    monkeypatch.setenv("STUB_MODE", mode)
    r = pipectl.mark_published(env["d1"], "u", PUB)
    assert r.status == status and not r.ok


def test_generic_error_shows_only_error_line_not_whole_stderr(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "error")
    r = pipectl.mark_published(env["d1"], "u", PUB)
    assert r.message == "KeyError: x" and "Traceback" not in r.message


def test_catalog_messages_are_vietnamese_one_liners(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "notready")
    m = pipectl.mark_published(env["d1"], "u", PUB).message
    assert "ERROR" not in m and "catalog not ready" not in m and "chưa đổi gì" in m and "1 phút" in m
    monkeypatch.setenv("STUB_MODE", "pending")
    m = pipectl.mark_published(env["d1"], "u", PUB).message
    assert "ERROR" not in m and "noise" not in m and "Thử lại ghi catalog" in m


def test_locked_message_is_vietnamese_with_hint(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "locked")
    m = pipectl.mark_published(env["d1"], "u", PUB).message
    assert "Explorer" in m and "CapCut" in m and "Studio" in m


def test_timeout_is_error(env, monkeypatch):
    def boom(args, timeout):
        raise pipectl.PipectlError("Quá thời gian chờ (300s): ")

    monkeypatch.setattr(pipectl, "_pipectl", boom)
    r = pipectl.mark_published(env["d1"], "u", PUB)
    assert r.status == pipectl.ERROR and "Quá thời gian" in r.message


def test_default_timeout_is_300(env):
    assert pipectl.MARK_TIMEOUT_S == 300


def test_real_pipectl_help_has_published_at_flag():
    if not (Path(cfg.YT_WF_PYTHON).exists() and Path(cfg.PIPECTL_PATH).exists()):
        pytest.skip("không có YT_WF_001")
    res = pipectl._pipectl(["mark-published", "--help"], 60)
    assert res.returncode == 0 and "--published-at" in res.stdout


# ---------------------------------------------------------------------------
# Dịch vụ xác nhận
# ---------------------------------------------------------------------------
def test_confirm_ok_writes_state_and_clears_pending(env):
    repo.update_state(env["db"], SLUG, 1, catalog_pending=1)
    r = confirm(env)
    assert r.ok
    st_ = repo.get_state(env["db"], SLUG, 1)
    assert st_["catalog_pending"] == 0 and st_["video_id"] == VID and st_["published_at"] == PUB
    a = [c for c in env["calls"]() if c[0] == "mark-published"][0]
    assert a[a.index("--published-at") + 1] == PUB and a[a.index("--url") + 1].endswith(VID)


def test_run_dir_is_refetched_after_folder_renamed(env):
    new = env["d1"].parent / (env["d1"].name + "__PUBLISHED")
    env["runs_file"].write_text(json.dumps({"runs": [run_entry(new, "001", "published")]}), encoding="utf-8")
    assert confirm(env).ok
    a = [c for c in env["calls"]() if c[0] == "mark-published"][0]
    assert a[a.index("--run-dir") + 1] == str(new)
    assert [c[0] for c in env["calls"]()][0] == "runs"  # runs mới ngay trước khi ghi


def test_catalog_pending_sets_flag_with_link_and_date(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "pending")
    r = confirm(env)
    assert r.status == pipectl.CATALOG_PENDING
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["catalog_pending"] == 1 and s["video_url"].endswith(VID) and s["published_at"] == PUB
    assert [p["number"] for p in repo.list_pending(env["db"])] == [1]
    # thẻ báo ghi thiếu
    assert us.video_status(kind="pipeline", run_state="published", has_link=True, catalog_pending=True) == us.GHI_THIEU


def test_retry_pending_clears_flag_and_uses_stored_values(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "pending")
    confirm(env)
    monkeypatch.setenv("STUB_MODE", "ok")
    r = svc.retry_pending(env["db"], SLUG, 1, env["root"])
    assert r.ok and repo.get_state(env["db"], SLUG, 1)["catalog_pending"] == 0
    last = [c for c in env["calls"]() if c[0] == "mark-published"][-1]
    assert last[last.index("--published-at") + 1] == PUB


def test_retry_failure_keeps_flag(env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "pending")
    confirm(env)
    r = svc.retry_pending(env["db"], SLUG, 1, env["root"])
    assert r.status == pipectl.CATALOG_PENDING and repo.get_state(env["db"], SLUG, 1)["catalog_pending"] == 1


@pytest.mark.parametrize("mode", ["notready", "locked", "error"])
def test_other_failures_do_not_set_pending(env, monkeypatch, mode):
    monkeypatch.setenv("STUB_MODE", mode)
    r = confirm(env)
    assert not r.ok
    assert (repo.get_state(env["db"], SLUG, 1) or {}).get("catalog_pending", 0) == 0
    assert not (repo.get_state(env["db"], SLUG, 1) or {}).get("video_url")


def test_channel_mismatch_has_no_write_path(env):
    r = confirm(env, vid="BBBBBBBBBBB")
    assert r.status == svc.BLOCKED and "khác" in r.message
    assert not [c for c in env["calls"]() if c[0] == "mark-published"]


def test_api_error_does_not_write(env):
    def boom(request):
        return httpx.Response(500, json={"error": {"message": "down"}})

    r = svc.confirm_pipeline(env["db"], SLUG, 1, VID, env["root"], api_key="k", transport=httpx.MockTransport(boom))
    assert r.status == svc.BLOCKED
    assert not [c for c in env["calls"]() if c[0] == "mark-published"]


def test_not_public_does_not_write(env):
    r = confirm(env, vid="PPPPPPPPPPP")
    assert r.status == svc.BLOCKED and "public" in r.message
    assert not [c for c in env["calls"]() if c[0] == "mark-published"]


def test_unbound_channel_does_not_write(env):
    env["db"].execute("DELETE FROM upload_channel")
    r = confirm(env)
    assert r.status == svc.BLOCKED and "gán" in r.message
    assert not [c for c in env["calls"]() if c[0] == "mark-published"]


def test_old_video_confirm_never_calls_pipectl(env):
    svc.confirm_old(env["db"], SLUG, 5, VID, PUB)
    s = repo.get_state(env["db"], SLUG, 5)
    assert s["video_url"].endswith(VID) and s["published_at"] == PUB and s["catalog_pending"] == 0
    assert env["calls"]() == []
    svc.unconfirm_old(env["db"], SLUG, 5)
    assert not repo.get_state(env["db"], SLUG, 5)["video_url"]
    assert env["calls"]() == []


def test_groups_and_weak_never_selectable():
    from yt_niche.core.upload_match import Proposal

    assert us.confirm_group("old", None) == us.GROUP_OLD
    assert us.confirm_group("pipeline", "published") == us.GROUP_SUPPLEMENT
    assert us.confirm_group("pipeline", "complete") == us.GROUP_RENAME
    weak = Proposal(SLUG, 1, VID, "weak", 0.9, "similar")
    strong = Proposal(SLUG, 1, VID, "strong", 1.0, "title")
    assert us.selectable_strong([weak], (SLUG, 1)) is None
    assert us.selectable_strong([weak, strong], (SLUG, 1)) is strong


# ---------------------------------------------------------------------------
# Trang
# ---------------------------------------------------------------------------
@pytest.fixture
def page_env(env, monkeypatch):
    common.get_conn.clear()
    st.cache_resource.clear()
    monkeypatch.setattr(common, "connect", lambda: env["db"])
    monkeypatch.setattr(svc, "refresh", lambda conn, slugs, **kw: svc.RefreshResult())
    real = svc.check_video_url
    monkeypatch.setattr(svc, "check_video_url", lambda conn, slug, url, **kw: real(conn, slug, url, api_key="k", transport=api_handler(good_videos())))
    real_c = svc.confirm_pipeline
    monkeypatch.setattr(svc, "confirm_pipeline", lambda conn, slug, n, vid, root, **kw: real_c(conn, slug, n, vid, root, api_key="k", transport=api_handler(good_videos())))
    yield env
    common.get_conn.clear()


def add_remotes(env):
    repo.replace_remote(env["db"], UC_A, [
        {"video_id": VID, "title": "Video Mot", "published_at": PUB},
        {"video_id": VID2, "title": "Video Hai", "published_at": "2026-11-03T01:02:03Z"},
    ])


def run_page() -> AppTest:
    at = AppTest.from_file(str(PAGE), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def mp_calls(env):
    return [c for c in env["calls"]() if c[0] == "mark-published"]


def test_rename_group_not_ticked_and_select_all_button(page_env):
    add_remotes(page_env)
    at = run_page()
    boxes = [c for c in at.checkbox if c.key.startswith(f"bat_{SLUG}_")]
    assert len(boxes) == 2 and not any(b.value for b in boxes)
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    assert all(c.value for c in at.checkbox if c.key.startswith(f"bat_{SLUG}_"))


def test_supplement_group_ticked_by_default(page_env):
    add_remotes(page_env)
    d = page_env["d1"].parent / (page_env["d1"].name + "__PUBLISHED")
    page_env["d1"].rename(d)
    page_env["runs_file"].write_text(json.dumps({"runs": [run_entry(d, "001", "published"), run_entry(page_env["d2"], "002")]}), encoding="utf-8")
    at = run_page()
    by = {c.key: c.value for c in at.checkbox if c.key.startswith(f"bat_{SLUG}_")}
    assert by[f"bat_{SLUG}_1"] is True and by[f"bat_{SLUG}_2"] is False


def test_batch_confirm_panel_warns_and_writes_nothing_until_run(page_env):
    add_remotes(page_env)
    at = run_page()
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    assert any("CapCut" in w_.value and "KHÔNG có lệnh huỷ" in w_.value for w_ in at.warning)
    assert not mp_calls(page_env)
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    calls = mp_calls(page_env)
    assert len(calls) == 2
    assert all(c[c.index("--published-at") + 1] for c in calls)


def test_batch_stops_at_first_error(page_env, monkeypatch):
    add_remotes(page_env)
    monkeypatch.setenv("STUB_FAIL_ON", "video-001")
    monkeypatch.setenv("STUB_FAIL_MODE", "error")
    at = run_page()
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    assert len(mp_calls(page_env)) == 1  # 001 lỗi → 002 chưa chạy
    assert any("Dừng ở lỗi đầu tiên" in e.value for e in at.error)


def test_weak_proposal_not_in_batch_but_single_confirm_exists(page_env):
    repo.replace_remote(page_env["db"], UC_A, [{"video_id": VID, "title": "Video Mot xyz extra words", "published_at": PUB}])
    at = run_page()
    assert not [c for c in at.checkbox if c.key.startswith("bat_")]
    stage = [b for b in at.button if (b.key or "").startswith(f"stage_{SLUG}_")]
    if stage:  # chỉ khi đề xuất yếu đủ ngưỡng
        stage[0].click().run()
        assert any("Xác nhận ghi" in m.value for m in at.markdown)
        assert not mp_calls(page_env)


def test_old_video_batch_does_not_call_pipectl(page_env):
    w(page_env["root"] / SLUG / "video-5-hello-world" / "YOUTUBE.md", "# YouTube kit — Hello World\n\n## Title\n1. Hello World\n")
    repo.replace_remote(page_env["db"], UC_A, [{"video_id": VID2, "title": "Hello World", "published_at": PUB}])
    at = run_page()
    assert at.checkbox(key=f"bat_{SLUG}_5").value is True
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    assert repo.get_state(page_env["db"], SLUG, 5)["video_id"] == VID2
    assert not mp_calls(page_env)


def test_manual_url_channel_mismatch_red_and_no_write_button(page_env):
    at = run_page()
    next(b for b in at.button if b.key == f"open_{SLUG}_1").click().run()
    at.text_input(key=f"man_url_{SLUG}_1").input("https://youtu.be/BBBBBBBBBBB").run()
    next(b for b in at.button if b.key == f"man_go_{SLUG}_1").click().run()
    assert any("kênh YouTube khác" in e.value for e in at.error)
    assert not [b for b in at.button if (b.key or "").startswith("man_stage_")]
    assert not mp_calls(page_env)


def test_manual_url_ok_then_confirm(page_env):
    at = run_page()
    next(b for b in at.button if b.key == f"open_{SLUG}_1").click().run()
    at.text_input(key=f"man_url_{SLUG}_1").input(f"https://youtu.be/{VID}").run()
    next(b for b in at.button if b.key == f"man_go_{SLUG}_1").click().run()
    next(b for b in at.button if b.key == f"man_stage_{SLUG}_1").click().run()
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    assert len(mp_calls(page_env)) == 1


def test_api_down_disables_write_button(page_env, monkeypatch):
    add_remotes(page_env)
    page_env["db"].execute("UPDATE upload_channel SET last_error = 'quota' WHERE channel_slug = ?", (SLUG,))
    at = run_page()
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    assert next(b for b in at.button if b.key == f"batch_run_{SLUG}").disabled


def test_open_page_retries_only_when_pending_exists(page_env, monkeypatch):
    run_page()
    assert not mp_calls(page_env)  # không có mục pending → không gọi gì ngoài runs
    st.cache_resource.clear()
    d = page_env["d1"].parent / (page_env["d1"].name + "__PUBLISHED")
    page_env["d1"].rename(d)
    page_env["runs_file"].write_text(json.dumps({"runs": [run_entry(d, "001", "published"), run_entry(page_env["d2"], "002")]}), encoding="utf-8")
    repo.confirm(page_env["db"], SLUG, 1, "https://www.youtube.com/watch?v=" + VID, VID, PUB, pending=True)
    at = run_page()
    calls = mp_calls(page_env)
    assert len(calls) == 1 and calls[0][calls[0].index("--run-dir") + 1] == str(d)
    assert repo.get_state(page_env["db"], SLUG, 1)["catalog_pending"] == 0
    at.run()
    assert len(mp_calls(page_env)) == 1  # một lần mỗi phiên, không lặp


def test_pending_card_shows_error_and_retry_button(page_env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "pending")
    d = page_env["d1"].parent / (page_env["d1"].name + "__PUBLISHED")
    page_env["d1"].rename(d)
    page_env["runs_file"].write_text(json.dumps({"runs": [run_entry(d, "001", "published"), run_entry(page_env["d2"], "002")]}), encoding="utf-8")
    repo.confirm(page_env["db"], SLUG, 1, "https://www.youtube.com/watch?v=" + VID, VID, PUB, pending=True)
    at = run_page()  # tự thử lại một lần, vẫn pending
    at.session_state["up_sel"] = (SLUG, 1)
    at.run()
    assert any("Ghi thiếu catalog" in e.value for e in at.error)
    n = len(mp_calls(page_env))
    monkeypatch.setenv("STUB_MODE", "ok")
    next(b for b in at.button if b.key == f"retry_{SLUG}_1").click().run()
    assert len(mp_calls(page_env)) == n + 1
    assert repo.get_state(page_env["db"], SLUG, 1)["catalog_pending"] == 0


def test_failure_result_is_in_a_dialog_not_page_top(page_env, monkeypatch):
    """Lỗi hiện trong hộp thoại «Kết quả ghi» (có nút Đóng), không chỉ ở đầu trang."""
    add_remotes(page_env)
    monkeypatch.setenv("STUB_MODE", "locked")
    at = run_page()
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    assert [b for b in at.button if b.key == f"batch_run_{SLUG}"]  # hộp xác nhận mở
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    assert any("Explorer" in e.value for e in at.error)
    assert [b for b in at.button if b.key == "result_close"]
    assert not [b for b in at.button if b.key == f"batch_run_{SLUG}"]
    next(b for b in at.button if b.key == "result_close").click().run()
    assert not [b for b in at.button if b.key == "result_close"]


def test_cancel_closes_confirm_dialog_and_writes_nothing(page_env):
    add_remotes(page_env)
    at = run_page()
    next(b for b in at.button if b.key == f"bat_all_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"bat_go_{SLUG}").click().run()
    next(b for b in at.button if b.key == f"batch_cancel_{SLUG}").click().run()
    assert not [b for b in at.button if b.key == f"batch_run_{SLUG}"]
    assert not mp_calls(page_env)
