"""Đánh dấu đã đăng tay, đổi tên sau 1 giờ (T6). pipectl giả bằng stub ghi tham số; không đụng folder thật, không đọc catalog thật,
không gọi API thật."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import streamlit as st  # noqa: F401  (page_env dùng)
from streamlit.testing.v1 import AppTest  # noqa: F401

from tests.test_upload_write import (  # noqa: F401  (fixtures: env, page_env)
    PAGE, PUB, SLUG, UC_A, VID, VID2, add_remotes, api_handler, env, good_videos, mp_calls, page_env, pipeline, run_entry,
    run_page, w,
)
from yt_niche.core import upload_state as us
from yt_niche.db import uploads as repo
from yt_niche.services import pipectl
from yt_niche.services import uploads as svc

NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
DUE = "2026-11-07T04:00:00Z"
FB_SLUG = "nhu-vay-toi-nghe"
FB_LINK = "https://www.facebook.com/CoNhanChiThuat/videos/1234567890"


def after_due():
    return NOW + timedelta(hours=1, minutes=1)


def set_runs(env, entries):
    env["runs_file"].write_text(json.dumps({"runs": entries}), encoding="utf-8")


def published_dir(env, key="d1"):
    d = env[key].parent / (env[key].name + "__PUBLISHED")
    env[key].rename(d)
    return d


def mark_calls(env):
    return [c for c in env["calls"]() if c[0] == "mark-published"]


def arg(call, name):
    return call[call.index(name) + 1]


def fb_env(env, slug=FB_SLUG, n=1):
    d = pipeline(env["root"] / slug, f"11-01_{slug}-video-00{n}-x", f"00{n}", f"Fb Video {n}")
    runs = json.loads(env["runs_file"].read_text(encoding="utf-8"))["runs"]
    runs.append(run_entry(d, f"00{n}"))
    set_runs(env, runs)
    return d


# ---------------------------------------------------------------------------
# Cơ chế chờ
# ---------------------------------------------------------------------------
def test_mark_later_writes_only_db_and_never_calls_pipectl(env):
    due = svc.mark_later(env["db"], SLUG, 1, now=NOW, run_state="complete")
    assert due == DUE
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["manual_state"] == us.MANUAL_WAITING_UPLOAD and s["due_at"] == DUE and s["published_at"] == DUE
    assert not s["video_url"] and not s["video_id"]
    assert env["calls"]() == []


def test_not_due_yet_calls_nothing(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    assert svc.run_due(env["db"], env["root"], now=NOW + timedelta(minutes=59)) == []
    assert env["calls"]() == []  # kể cả `runs`


def test_due_calls_once_with_saved_date_and_empty_url(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    res = svc.run_due(env["db"], env["root"], now=after_due())
    assert [r.status for r in res] == [pipectl.OK]
    calls = mark_calls(env)
    assert len(calls) == 1
    assert arg(calls[0], "--url") == "" and arg(calls[0], "--published-at") == DUE
    assert arg(calls[0], "--run-dir") == str(env["d1"])
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["manual_state"] == "" and not s["video_url"] and not s["video_id"]
    assert svc.run_due(env["db"], env["root"], now=after_due()) == []  # không chạy lần hai
    assert len(mark_calls(env)) == 1


def test_app_opened_two_days_late_keeps_saved_date(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    svc.run_due(env["db"], env["root"], now=NOW + timedelta(days=2))
    assert arg(mark_calls(env)[0], "--published-at") == DUE


@pytest.mark.parametrize("mode,status", [("locked", pipectl.LOCKED), ("notready", pipectl.CATALOG_NOT_READY)])
def test_locked_and_catalog_not_ready_are_kept_for_next_time(env, monkeypatch, mode, status):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", mode)
    res = svc.run_due(env["db"], env["root"], now=after_due())
    assert res[0].status == status and not res[0].resolved
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["manual_state"] == us.MANUAL_WAITING_UPLOAD and s["due_at"] == DUE and not s["catalog_pending"]
    monkeypatch.setenv("STUB_MODE", "ok")  # lần sau thành công, vẫn đúng ngày đã lưu
    assert svc.run_due(env["db"], env["root"], now=after_due() + timedelta(minutes=6))[0].status == pipectl.OK
    assert arg(mark_calls(env)[-1], "--published-at") == DUE


def test_other_error_is_kept_and_reported(env, monkeypatch):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", "error")
    r = svc.run_due(env["db"], env["root"], now=after_due())[0]
    assert r.status == pipectl.ERROR and r.message == "KeyError: x"
    assert repo.get_state(env["db"], SLUG, 1)["manual_state"] == us.MANUAL_WAITING_UPLOAD


def test_catalog_pending_sets_flag_and_retry_uses_no_link_branch(env, monkeypatch):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", "pending")
    r = svc.run_due(env["db"], env["root"], now=after_due())[0]
    assert r.status == pipectl.CATALOG_PENDING and r.resolved
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["catalog_pending"] == 1 and s["manual_state"] == "" and s["published_at"] == DUE and not s["video_id"]
    assert us.video_status(kind="pipeline", run_state="published", catalog_pending=True) == us.GHI_THIEU
    monkeypatch.setenv("STUB_MODE", "ok")
    ok = svc.retry_pending(env["db"], SLUG, 1, env["root"])
    assert ok.ok
    last = mark_calls(env)[-1]
    assert arg(last, "--url") == "" and arg(last, "--published-at") == DUE
    assert repo.get_state(env["db"], SLUG, 1)["catalog_pending"] == 0


def test_retry_without_link_failure_keeps_flag(env, monkeypatch):
    repo.update_state(env["db"], SLUG, 1, catalog_pending=1, published_at=DUE)
    monkeypatch.setenv("STUB_MODE", "pending")
    assert svc.retry_pending(env["db"], SLUG, 1, env["root"]).status == pipectl.CATALOG_PENDING
    assert repo.get_state(env["db"], SLUG, 1)["catalog_pending"] == 1


def test_retry_without_link_and_without_date_is_error(env):
    repo.update_state(env["db"], SLUG, 1, catalog_pending=1)
    assert svc.retry_pending(env["db"], SLUG, 1, env["root"]).status == pipectl.ERROR
    assert mark_calls(env) == []


def test_run_already_published_is_not_overwritten(env):
    """Bẫy đã thấy: lệnh hoãn chạy SAU khi đã có ngày thật sẽ ghi đè bằng ngày ước lượng. Phải không gọi lệnh."""
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    d = published_dir(env)
    set_runs(env, [run_entry(d, "001", "published"), run_entry(env["d2"], "002")])
    res = svc.run_due(env["db"], env["root"], now=after_due())
    assert [r.status for r in res] == [svc.SKIPPED]
    assert mark_calls(env) == []
    assert repo.get_state(env["db"], SLUG, 1)["manual_state"] == ""


def test_run_not_complete_is_kept_without_call(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    set_runs(env, [run_entry(env["d1"], "001", "in_flight"), run_entry(env["d2"], "002")])
    r = svc.run_due(env["db"], env["root"], now=after_due())[0]
    assert r.status == svc.KEPT and mark_calls(env) == []
    assert repo.get_state(env["db"], SLUG, 1)["manual_state"] == us.MANUAL_WAITING_UPLOAD


def test_mark_later_rejects_unfinished_run_and_double_mark(env):
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], SLUG, 1, now=NOW, run_state="published")
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], SLUG, 1, now=NOW)


def test_t5_confirm_during_wait_cancels_deferred_command(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    r = svc.confirm_pipeline(env["db"], SLUG, 1, VID, env["root"], api_key="k", transport=api_handler(good_videos()))
    assert r.ok
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["manual_state"] == "" and not s["due_at"]
    n = len(mark_calls(env))
    set_runs(env, [run_entry(env["d1"], "001", "published"), run_entry(env["d2"], "002")])
    assert svc.run_due(env["db"], env["root"], now=NOW + timedelta(days=1)) == []
    assert len(mark_calls(env)) == n


def test_run_now_uses_click_time_and_cancel_clears(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    r = svc.run_now(env["db"], env["root"], SLUG, 1, now=NOW + timedelta(minutes=5))
    assert r.status == pipectl.OK
    assert arg(mark_calls(env)[0], "--published-at") == "2026-11-07T03:05:00Z"
    assert arg(mark_calls(env)[0], "--url") == ""
    svc.mark_later(env["db"], SLUG, 2, now=NOW)
    svc.cancel_mark(env["db"], SLUG, 2)
    s = repo.get_state(env["db"], SLUG, 2)
    assert s["manual_state"] == "" and not s["due_at"] and not s["published_at"]
    assert svc.run_due(env["db"], env["root"], now=after_due()) == []
    assert len(mark_calls(env)) == 1


def test_run_now_not_waiting_does_nothing(env):
    assert svc.run_now(env["db"], env["root"], SLUG, 1, now=NOW).status == svc.KEPT
    assert env["calls"]() == []


# ---------------------------------------------------------------------------
# verify_for_write không bị nới lỏng; đường hoãn không bao giờ đặt youtube_url
# ---------------------------------------------------------------------------
def test_deferred_path_never_calls_verify_for_write_or_sets_url(env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("verify_for_write không được gọi trên đường đánh dấu tay")

    monkeypatch.setattr(svc, "verify_for_write", boom)
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    svc.run_due(env["db"], env["root"], now=after_due())
    svc.mark_later(env["db"], SLUG, 2, now=NOW)
    svc.run_now(env["db"], env["root"], SLUG, 2, now=NOW)
    for c in mark_calls(env):
        assert arg(c, "--url") == ""
    for n in (1, 2):
        s = repo.get_state(env["db"], SLUG, n)
        assert not s["video_url"] and not s["video_id"]


def test_confirm_pipeline_still_blocks_after_manual_mark(env):
    """Sau khi đánh dấu tay: link kênh khác, API lỗi, video private đều vẫn bị chặn."""
    import httpx

    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    svc.run_due(env["db"], env["root"], now=after_due())
    set_runs(env, [run_entry(published_dir(env), "001", "published"), run_entry(env["d2"], "002")])
    n = len(mark_calls(env))
    for vid in ("BBBBBBBBBBB", "PPPPPPPPPPP"):
        r = svc.confirm_pipeline(env["db"], SLUG, 1, vid, env["root"], api_key="k", transport=api_handler(good_videos()))
        assert r.status == svc.BLOCKED
    down = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": {"message": "down"}}))
    assert svc.confirm_pipeline(env["db"], SLUG, 1, VID, env["root"], api_key="k", transport=down).status == svc.BLOCKED
    assert len(mark_calls(env)) == n


def test_supplement_link_after_manual_mark_replaces_estimate_with_real_date(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    svc.run_due(env["db"], env["root"], now=after_due())
    d = published_dir(env)
    set_runs(env, [run_entry(d, "001", "published"), run_entry(env["d2"], "002")])
    # chưa có link, chưa có đề xuất
    assert us.video_status(kind="pipeline", run_state="published", has_link=False) == us.DA_DANG_CHUA_LINK
    # có đề xuất → cần xác nhận, nhóm bổ sung link
    assert us.video_status(kind="pipeline", run_state="published", has_proposal=True) == us.CAN_XAC_NHAN
    assert us.confirm_group("pipeline", "published") == us.GROUP_SUPPLEMENT
    r = svc.confirm_pipeline(env["db"], SLUG, 1, VID, env["root"], api_key="k", transport=api_handler(good_videos()))
    assert r.ok
    last = mark_calls(env)[-1]
    assert arg(last, "--published-at") == PUB and arg(last, "--url").endswith(VID)
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["video_url"].endswith(VID)
    assert us.video_status(kind="pipeline", run_state="published", has_link=bool(s["video_url"])) == us.XONG


# ---------------------------------------------------------------------------
# YouTube: không nhận link/ngày nhập tay
# ---------------------------------------------------------------------------
def test_youtube_rejects_facebook_link_and_manual_date(env):
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], SLUG, 1, fb_url=FB_LINK, now=NOW)
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], SLUG, 1, published_at="2026-11-05T05:00:00Z", now=NOW)
    assert repo.get_state(env["db"], SLUG, 1) is None
    assert env["calls"]() == []


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------
def test_facebook_click_with_nothing_reaches_xong(env):
    d = fb_env(env)
    svc.mark_later(env["db"], FB_SLUG, 1, now=NOW)
    s = repo.get_state(env["db"], FB_SLUG, 1)
    assert s["published_at"] == DUE and not s["fb_url"]
    res = svc.run_due(env["db"], env["root"], now=after_due())
    assert res[0].status == pipectl.OK
    c = mark_calls(env)[0]
    assert arg(c, "--run-dir") == str(d) and arg(c, "--url") == "" and arg(c, "--published-at") == DUE
    assert us.video_status(kind="pipeline", run_state="published", platform=us.FACEBOOK) == us.XONG


def test_facebook_link_never_reaches_url_argument(env, monkeypatch):
    fb_env(env, n=1)
    fb_env(env, n=2)
    svc.mark_later(env["db"], FB_SLUG, 1, fb_url=FB_LINK, published_at="2026-11-05T05:00:00Z", now=NOW)
    svc.run_due(env["db"], env["root"], now=after_due())
    svc.mark_later(env["db"], FB_SLUG, 2, fb_url=FB_LINK + "9", now=NOW)
    svc.run_now(env["db"], env["root"], FB_SLUG, 2, now=NOW)
    r = svc.set_fb_date(env["db"], FB_SLUG, 1, "2026-11-04T05:00:00Z", env["root"], now=NOW)
    assert r.ok
    monkeypatch.setenv("STUB_MODE", "pending")
    repo.update_state(env["db"], FB_SLUG, 1, catalog_pending=1)
    svc.retry_pending(env["db"], FB_SLUG, 1, env["root"])
    calls = mark_calls(env)
    assert len(calls) == 4
    for c in calls:
        assert arg(c, "--url") == "" and "facebook.com" not in " ".join(c)
    assert arg(calls[0], "--published-at") == "2026-11-05T05:00:00Z"  # ngày nhập tay được giữ
    assert arg(calls[2], "--published-at") == "2026-11-04T05:00:00Z"
    assert repo.get_state(env["db"], FB_SLUG, 1)["fb_url"] == FB_LINK  # link chỉ ở DB app


def test_facebook_future_date_rejected(env):
    fb_env(env)
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], FB_SLUG, 1, published_at="2026-11-09T05:00:00Z", now=NOW)
    r = svc.set_fb_date(env["db"], FB_SLUG, 1, "2026-11-09T05:00:00Z", env["root"], now=NOW)
    assert r.status == pipectl.ERROR and mark_calls(env) == []


@pytest.mark.parametrize("bad", [
    "https://www.facebook.com/CoNhanChiThuat/",
    "https://www.youtube.com/watch?v=AAAAAAAAAAA",
    "https://facebook.com.evil.com/CoNhanChiThuat/videos/1",
])
def test_facebook_bad_links_rejected_and_nothing_written(env, bad):
    fb_env(env)
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], FB_SLUG, 1, fb_url=bad, now=NOW)
    assert repo.get_state(env["db"], FB_SLUG, 1) is None


def test_duplicate_link_blocked_only_when_link_present(env):
    fb_env(env, n=1)
    fb_env(env, n=2)
    svc.mark_later(env["db"], FB_SLUG, 1, now=NOW)
    svc.mark_later(env["db"], FB_SLUG, 2, now=NOW)  # cả hai không link: được
    svc.cancel_mark(env["db"], FB_SLUG, 1)
    svc.cancel_mark(env["db"], FB_SLUG, 2)
    svc.mark_later(env["db"], FB_SLUG, 1, fb_url=FB_LINK, now=NOW)
    with pytest.raises(svc.UploadError):
        svc.mark_later(env["db"], FB_SLUG, 2, fb_url=FB_LINK.replace("www.", "m.") + "/", now=NOW)


def test_set_fb_link_db_only(env):
    fb_env(env)
    svc.set_fb_link(env["db"], FB_SLUG, 1, FB_LINK)
    assert repo.get_state(env["db"], FB_SLUG, 1)["fb_url"] == FB_LINK
    svc.set_fb_link(env["db"], FB_SLUG, 1, "")
    assert not repo.get_state(env["db"], FB_SLUG, 1)["fb_url"]
    with pytest.raises(svc.UploadError):
        svc.set_fb_link(env["db"], SLUG, 1, FB_LINK)  # kênh YouTube
    assert env["calls"]() == []


def test_thau_without_page_url_works(env):
    fb_env(env, slug="thau")
    assert svc.page_url_of(env["db"], "thau") == ""
    svc.mark_later(env["db"], "thau", 1, now=NOW)
    assert svc.run_due(env["db"], env["root"], now=after_due())[0].status == pipectl.OK


def test_old_facebook_video_never_calls_pipectl(env):
    svc.confirm_facebook_old(env["db"], FB_SLUG, 7, fb_url=FB_LINK, now=NOW)
    s = repo.get_state(env["db"], FB_SLUG, 7)
    assert s["manual_state"] == us.MANUAL_FB_DONE and s["fb_url"] == FB_LINK and s["published_at"] == "2026-11-07T03:00:00Z"
    assert us.video_status(kind="old", manual_state=s["manual_state"], platform=us.FACEBOOK) == us.XONG
    svc.unconfirm_facebook_old(env["db"], FB_SLUG, 7)
    assert repo.get_state(env["db"], FB_SLUG, 7)["manual_state"] == ""
    with pytest.raises(svc.UploadError):
        svc.confirm_facebook_old(env["db"], SLUG, 7)  # kênh YouTube: video cũ không có đường đánh dấu tay
    assert env["calls"]() == []


# ---------------------------------------------------------------------------
# Trang
# ---------------------------------------------------------------------------
def open_card(at, slug, n):
    next(b for b in at.button if b.key == f"open_{slug}_{n}").click().run()
    return at


def test_page_mark_flow_youtube_writes_nothing_until_due(page_env):
    at = open_card(run_page(), SLUG, 1)
    # hai nút khác nhau, có chú thích phân biệt
    keys = [b.key for b in at.button]
    assert f"mark_{SLUG}_1" in keys and f"wait_{SLUG}_1" in keys
    next(b for b in at.button if b.key == f"mark_{SLUG}_1").click().run()
    assert any("chưa kiểm được video này thuộc đúng kênh" in e.value for e in at.error)  # dòng đỏ của YouTube
    assert any("KHÔNG có lệnh huỷ" in x.value for x in at.warning)
    assert repo.get_state(page_env["db"], SLUG, 1) is None  # mới mở hộp, chưa ghi
    next(b for b in at.button if b.key == f"mark_run_{SLUG}").click().run()
    s = repo.get_state(page_env["db"], SLUG, 1)
    assert s["manual_state"] == us.MANUAL_WAITING_UPLOAD
    assert not mp_calls(page_env)
    at.session_state["up_sel"] = (SLUG, 1)
    at.run()
    keys = [b.key for b in at.button]
    assert f"now_{SLUG}_1" in keys and f"cancelmark_{SLUG}_1" in keys
    assert any("Sẽ đổi tên lúc" in i.value for i in at.info)
    next(b for b in at.button if b.key == f"cancelmark_{SLUG}_1").click().run()
    assert repo.get_state(page_env["db"], SLUG, 1)["manual_state"] == ""
    assert not mp_calls(page_env)


def test_page_due_item_runs_once_after_render(page_env):
    repo.update_state(page_env["db"], SLUG, 1, manual_state=us.MANUAL_WAITING_UPLOAD, due_at="2026-01-01T00:00:00Z",
                      published_at="2026-01-01T00:00:00Z")
    at = run_page()
    calls = mp_calls(page_env)
    assert len(calls) == 1 and arg(calls[0], "--url") == "" and arg(calls[0], "--published-at") == "2026-01-01T00:00:00Z"
    assert repo.get_state(page_env["db"], SLUG, 1)["manual_state"] == ""
    at.run()
    assert len(mp_calls(page_env)) == 1


def test_page_open_without_waiting_calls_only_runs(page_env):
    run_page()
    assert page_env["calls"]() and all(c[0] == "runs" for c in page_env["calls"]())


def test_page_facebook_channel_no_api_and_no_youtube_ui(page_env, monkeypatch):
    fb_env(page_env)
    seen = []
    monkeypatch.setattr(svc, "check_video_url", lambda *a, **k: seen.append("check") or {})
    monkeypatch.setattr(svc, "refresh", lambda conn, slugs, **kw: seen.append(("refresh", list(slugs))) or svc.RefreshResult())
    monkeypatch.setattr(svc, "preview_binding", lambda *a, **k: seen.append("preview"))
    at = run_page()
    seen.clear()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    assert not at.exception
    open_card(at, FB_SLUG, 1)
    assert seen == [] or all(x != "preview" and x != "check" for x in seen)
    assert all(not (isinstance(x, tuple) and FB_SLUG in x[1]) for x in seen)
    assert not [b for b in at.button if (b.key or "").startswith("bind_")]
    assert not [e for e in at.expander if "Khai kênh YouTube" in e.label]
    assert any(c[0] == "runs" for c in page_env["calls"]()) and all(c[0] == "runs" for c in page_env["calls"]())
    assert f"mark_{FB_SLUG}_1" in [b.key for b in at.button]
    assert not [b for b in at.button if b.key == f"wait_{FB_SLUG}_1"]
    assert not [i for i in at.error if "chưa kiểm được video" in i.value]


def test_page_facebook_mark_with_nothing_then_due_reaches_xong(page_env):
    fb_env(page_env)
    at = run_page()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    open_card(at, FB_SLUG, 1)
    next(b for b in at.button if b.key == f"mark_{FB_SLUG}_1").click().run()
    assert not any("chưa kiểm được video" in e.value for e in at.error)  # dòng đỏ chỉ của YouTube
    next(b for b in at.button if b.key == f"mark_run_{FB_SLUG}").click().run()
    assert repo.get_state(page_env["db"], FB_SLUG, 1)["manual_state"] == us.MANUAL_WAITING_UPLOAD
    assert not mp_calls(page_env)


def test_page_facebook_bad_link_shows_error(page_env):
    fb_env(page_env)
    at = run_page()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    open_card(at, FB_SLUG, 1)
    at.text_input(key=f"fbl_{FB_SLUG}_1").input("https://www.facebook.com/CoNhanChiThuat/").run()
    assert any("chỉ tới trang" in e.value for e in at.error)


def test_page_old_facebook_video_marks_db_only(page_env):
    w(page_env["root"] / FB_SLUG / "video-5-hello" / "YOUTUBE.md", "# YouTube kit — Hello\n\n## Title\n1. Hello\n")
    set_runs(page_env, json.loads(page_env["runs_file"].read_text(encoding="utf-8"))["runs"])
    at = run_page()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    open_card(at, FB_SLUG, 5)
    next(b for b in at.button if b.key == f"fbold_{FB_SLUG}_5").click().run()
    assert repo.get_state(page_env["db"], FB_SLUG, 5)["manual_state"] == us.MANUAL_FB_DONE
    assert not mp_calls(page_env)


def test_page_has_no_batch_path_for_mark_later():
    src = PAGE.read_text(encoding="utf-8")
    assert "mark_later" in src and "verify_for_write" not in src


# ---------------------------------------------------------------------------
# Nghiệm thu lần 1: 4 việc sửa
# ---------------------------------------------------------------------------
def test_failed_try_sets_next_try_and_is_skipped_until_then(env, monkeypatch):
    """Việc 1: lệnh hoãn thất bại thì không được gọi lại ngay; đợi ~5 phút (lưu trong DB)."""
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", "locked")
    t = after_due()
    svc.run_due(env["db"], env["root"], now=t)
    s = repo.get_state(env["db"], SLUG, 1)
    assert s["next_try_at"] == "2026-11-07T04:06:00Z" and "Explorer" in s["try_error"]
    for dt in (0, 30, 299):
        assert svc.run_due(env["db"], env["root"], now=t + timedelta(seconds=dt)) == []
    assert len(mark_calls(env)) == 1
    svc.run_due(env["db"], env["root"], now=t + timedelta(minutes=5, seconds=1))
    assert len(mark_calls(env)) == 2


@pytest.mark.parametrize("mode", ["notready", "error"])
def test_every_failure_kind_sets_retry_window(env, monkeypatch, mode):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", mode)
    svc.run_due(env["db"], env["root"], now=after_due())
    assert repo.get_state(env["db"], SLUG, 1)["next_try_at"]
    assert svc.due_items(env["db"], after_due() + timedelta(minutes=1)) == []


def test_kept_run_not_complete_sets_retry_window(env):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    set_runs(env, [run_entry(env["d1"], "001", "in_flight"), run_entry(env["d2"], "002")])
    svc.run_due(env["db"], env["root"], now=after_due())
    assert repo.get_state(env["db"], SLUG, 1)["next_try_at"]


def test_run_now_ignores_retry_window_and_success_clears_it(env, monkeypatch):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", "locked")
    svc.run_due(env["db"], env["root"], now=after_due())
    monkeypatch.setenv("STUB_MODE", "ok")
    assert svc.run_now(env["db"], env["root"], SLUG, 1, now=after_due() + timedelta(seconds=10)).status == pipectl.OK
    s = repo.get_state(env["db"], SLUG, 1)
    assert not s["next_try_at"] and not s["try_error"] and s["manual_state"] == ""


def test_cancel_clears_retry_info(env, monkeypatch):
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    monkeypatch.setenv("STUB_MODE", "locked")
    svc.run_due(env["db"], env["root"], now=after_due())
    svc.cancel_mark(env["db"], SLUG, 1)
    s = repo.get_state(env["db"], SLUG, 1)
    assert not s["next_try_at"] and not s["try_error"]


def make_due(env, n=1):
    repo.update_state(env["db"], SLUG, n, manual_state=us.MANUAL_WAITING_UPLOAD, due_at="2026-01-01T00:00:00Z",
                      published_at="2026-01-01T00:00:00Z")


def test_page_loop_locked_calls_command_once_across_many_reruns(page_env, monkeypatch):
    """Việc 1, dựng đúng vòng chạy của trang: nhiều lần chạy lại liên tiếp chỉ gọi lệnh một lần."""
    monkeypatch.setenv("STUB_MODE", "locked")
    make_due(page_env)
    at = run_page()
    for _ in range(5):
        at.run()
        assert not at.exception
    assert len(mp_calls(page_env)) == 1
    s = repo.get_state(page_env["db"], SLUG, 1)
    assert s["manual_state"] == us.MANUAL_WAITING_UPLOAD and s["next_try_at"]
    at.session_state["up_sel"] = (SLUG, 1)
    at.run()
    assert any("Chưa đổi tên được" in x.value and "Sẽ thử lại lúc" in x.value for x in at.warning)
    assert len(mp_calls(page_env)) == 1


def test_page_retries_after_window_and_now_button_calls_immediately(page_env, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "locked")
    make_due(page_env)
    at = run_page()
    assert len(mp_calls(page_env)) == 1
    repo.update_state(page_env["db"], SLUG, 1, next_try_at="2026-01-01T00:00:00Z")  # hết thời gian đợi
    at.run()
    assert len(mp_calls(page_env)) == 2
    repo.update_state(page_env["db"], SLUG, 1, next_try_at="2999-01-01T00:00:00Z")  # đang đợi
    monkeypatch.setenv("STUB_MODE", "ok")
    at.session_state["up_sel"] = (SLUG, 1)
    at.run()
    assert len(mp_calls(page_env)) == 2
    next(b for b in at.button if b.key == f"now_{SLUG}_1").click().run()
    assert len(mp_calls(page_env)) == 3
    assert repo.get_state(page_env["db"], SLUG, 1)["manual_state"] == ""


def test_due_watch_does_not_rerun_on_first_build(page_env):
    """Mục sẵn sàng + lần dựng trang đầu: fragment chỉ lên cò, không tự st.rerun."""
    make_due(page_env)
    at = run_page()
    assert at.session_state["_up_due_armed"] is True
    assert len(mp_calls(page_env)) == 1


def test_waiting_block_shown_even_when_strong_proposal_exists(page_env):
    """Việc 2: video đang chờ mà có đề xuất mạnh vẫn có «Sẽ đổi tên lúc», «Ghi ngay», «Huỷ»; ẩn nút đánh dấu."""
    add_remotes(page_env)
    svc.mark_later(page_env["db"], SLUG, 1, now=datetime.now(timezone.utc))
    at = run_page()
    at.session_state["up_sel"] = (SLUG, 1)
    at.run()
    keys = [b.key for b in at.button]
    assert f"now_{SLUG}_1" in keys and f"cancelmark_{SLUG}_1" in keys
    assert f"mark_{SLUG}_1" not in keys
    assert any("Sẽ đổi tên lúc" in i.value for i in at.info)
    assert any((b.key or "").startswith(f"stage_{SLUG}_1_") for b in at.button)  # vẫn xác nhận được
    next(b for b in at.button if (b.key or "").startswith(f"stage_{SLUG}_1_")).click().run()
    next(b for b in at.button if b.key == f"batch_run_{SLUG}").click().run()
    assert repo.get_state(page_env["db"], SLUG, 1)["manual_state"] == ""  # lệnh hoãn tự huỷ


def test_close_result_dialog_keeps_selected_channel_and_card(page_env):
    """Việc 3: đóng hộp kết quả không đưa về kênh đầu."""
    fb_env(page_env)
    at = run_page()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    open_card(at, FB_SLUG, 1)
    at.session_state["up_result"] = (["xong"], [])
    at.run()
    next(b for b in at.button if b.key == "result_close").click().run()
    assert at.radio(key="up_chan").value == FB_SLUG
    assert at.session_state["up_sel"] == (FB_SLUG, 1)


def test_confirm_dialog_flow_keeps_channel(page_env):
    fb_env(page_env)
    at = run_page()
    at.radio(key="up_chan").set_value(FB_SLUG).run()
    open_card(at, FB_SLUG, 1)
    next(b for b in at.button if b.key == f"mark_{FB_SLUG}_1").click().run()
    next(b for b in at.button if b.key == f"mark_run_{FB_SLUG}").click().run()
    next(b for b in at.button if b.key == "result_close").click().run()
    assert at.radio(key="up_chan").value == FB_SLUG


def test_skipped_updates_db_date_to_project_json_date(env):
    """Việc 4: run đã published khi đến hạn → ngày trong DB app theo project.json (ngày thật)."""
    svc.mark_later(env["db"], SLUG, 1, now=NOW)
    d = published_dir(env)
    doc = json.loads((d / "project.json").read_text(encoding="utf-8"))
    doc["published_at"] = "2026-11-05T09:00:00Z"
    (d / "project.json").write_text(json.dumps(doc), encoding="utf-8")
    set_runs(env, [run_entry(d, "001", "published"), run_entry(env["d2"], "002")])
    assert svc.run_due(env["db"], env["root"], now=after_due())[0].status == svc.SKIPPED
    assert repo.get_state(env["db"], SLUG, 1)["published_at"] == "2026-11-05T09:00:00Z"
    assert mark_calls(env) == []
