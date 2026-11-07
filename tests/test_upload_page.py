"""Trang Upload trên cây giả + DB tạm. pipectl và refresh được giả lập; không gọi API, không đụng catalog."""

import json
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from yt_niche import config as cfg
from yt_niche.db import uploads as repo
from yt_niche.db.connection import connect
from yt_niche.services import pipectl
from yt_niche.services import uploads as upl
from yt_niche.ui import common

PAGE = Path(__file__).resolve().parent.parent / "yt_niche" / "ui" / "pages" / "upload_page.py"
SLUG = "chan-a"
CID = "UCaaaaaaaaaaaaaaaaaaaaaa"
TITLE = "タイトル  二重スペース"
DESC = "  行頭の空白\n\n  内側  のスペース  \n末尾  "


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


def pipeline(cdir: Path, name: str, vid: str, *, mp4=True, title=TITLE, desc=DESC, thumbs=("thumbnail.jpg",)):
    d = cdir / name
    w(d / "project.json", json.dumps({"video_id": vid, "channel_slug": cdir.name}))
    pub = {"title": {"primary": title, "en": "EN"}, "description": {"primary": desc, "en": "d-en"}}
    w(d / "artifacts" / "publish.json", json.dumps(pub, ensure_ascii=False))
    if mp4:
        w(d / "final.mp4", b"\0")
    for t in thumbs:
        png(d / t, t)
    return d


def entry(folder: Path, vid: str, state="complete"):
    return {
        "run_dir": str(folder), "channel_slug": folder.parent.name, "video_id": vid, "state": state,
        "lifecycle": "published" if state == "published" else "active", "created": "2026-11-01T00:00:00Z",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "social"
    cdir = root / SLUG
    d1 = pipeline(cdir, "11-01_chan-a-video-001-x", "001", thumbs=("thumbnail.jpg", "thumbnail_B.jpg", "thumbnails.jpg"))
    d2 = pipeline(cdir, "11-02_chan-a-video-002-y", "002", mp4=False, title="Hai", desc="d2")
    runs = {"runs": [entry(d1, "001"), entry(d2, "002")], "counts": {}}
    calls = []

    def fake(args, timeout):
        calls.append(list(args))
        return pipectl.CmdResult(0, json.dumps(runs), "")

    monkeypatch.setattr(pipectl, "_pipectl", fake)
    monkeypatch.setattr(cfg, "SOCIAL_MEDIA_ROOT", root)
    monkeypatch.setattr(cfg, "YT_WF_ROOT", tmp_path / "wf")
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    db = connect(tmp_path / "app.db")
    common.get_conn.clear()
    st.cache_resource.clear()
    monkeypatch.setattr(common, "connect", lambda: db)
    refresh_calls = []
    monkeypatch.setattr(upl, "refresh", lambda conn, slugs, **kw: refresh_calls.append(list(slugs)) or upl.RefreshResult())
    yield {"root": root, "db": db, "calls": calls, "refresh": refresh_calls, "d1": d1, "d2": d2}
    common.get_conn.clear()


def bind(db, fresh=True):
    info = {"channel_id": CID, "handle": "@a", "title": "Real A", "uploads_playlist": "UUa", "thumb_url": ""}
    repo.upsert_channel(db, SLUG, info)
    if fresh:
        db.execute("UPDATE upload_channel SET last_refresh_at = ? WHERE channel_slug = ?", (repo.now_iso(), SLUG))


def run_page() -> AppTest:
    at = AppTest.from_file(str(PAGE), default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def open_card(at: AppTest, number: int) -> AppTest:
    next(b for b in at.button if b.key == f"open_{SLUG}_{number}").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_open_calls_only_pipectl_runs_and_never_api_when_snapshot_fresh(env):
    bind(env["db"])
    run_page()
    assert env["calls"] and all(c[0] == "runs" for c in env["calls"])
    assert env["refresh"] == []


def test_stale_snapshot_refreshes_once_per_session(env):
    bind(env["db"], fresh=False)
    at = run_page()
    assert env["refresh"] == [[SLUG]]
    at.run()
    assert env["refresh"] == [[SLUG]]


def test_page_source_never_touches_catalog_or_mark_published():
    src = PAGE.read_text(encoding="utf-8")
    for bad in ("catalog_db", "mark_published", "mark-published", "subprocess", "st.video"):
        assert bad not in src


def test_code_blocks_match_publish_json_byte_for_byte(env):
    at = open_card(run_page(), 1)
    codes = [c.value for c in at.code]
    assert TITLE in codes and DESC in codes
    assert str(env["d1"] / "final.mp4") in codes


def test_collage_not_in_grid_and_selection_remembered(env):
    at = open_card(run_page(), 1)
    assert not any("thumbnails.jpg" in c.value for c in at.code)
    boxes = [c for c in at.checkbox if c.key.startswith(f"th_{SLUG}_1_")]
    assert len(boxes) == 2
    boxes[1].check().run()
    assert repo.get_state(env["db"], SLUG, 1)["thumb_choice"] == ["thumbnail_B.jpg"]
    again = [c for c in open_card(run_page(), 1).checkbox if c.key.startswith(f"th_{SLUG}_1_")]
    assert [c.value for c in again] == [False, True]


def test_fourth_thumbnail_is_blocked(env):
    for n in ("c.jpg", "d.jpg"):
        png(env["d1"] / n, n)
    at = open_card(run_page(), 1)
    keys = [c.key for c in at.checkbox if c.key.startswith(f"th_{SLUG}_1_")]
    assert len(keys) == 4
    for k in keys[:3]:
        at.checkbox(key=k).check().run()
    at.checkbox(key=keys[3]).check().run()
    assert len(repo.get_state(env["db"], SLUG, 1)["thumb_choice"]) == 3
    assert any("tối đa 3" in x.value.lower() for x in at.warning)


def test_missing_stuff_is_reported(env):
    at = open_card(run_page(), 2)
    assert any("Thiếu đồ" in e.value and "mp4" in e.value for e in at.error)


def test_wait_public_button_writes_only_app_db(env):
    at = open_card(run_page(), 1)
    next(b for b in at.button if b.key == f"wait_{SLUG}_1").click().run()
    assert repo.get_state(env["db"], SLUG, 1)["manual_state"] == "waiting_public"
    assert not (env["d1"].parent / (env["d1"].name + "__PUBLISHED")).exists()


def test_no_write_button_without_proposal(env):
    """Chưa có đề xuất (hoặc video lên tay chưa kiểm) thì không có nút ghi nào."""
    bind(env["db"])
    at = open_card(run_page(), 1)
    # T6: «Đã đăng, đổi tên sau 1 giờ» chỉ ghi DB app (hoãn), không phải nút ghi local; bỏ nó khỏi phép kiểm này.
    labels = " | ".join(b.label.lower() for b in at.button if not (b.key or "").startswith("mark_"))
    for bad in ("ghi vào máy", "xác nhận video", "mark", "đổi tên"):
        assert bad not in labels


def test_binding_with_wrong_name_shows_red_warning_and_writes_only_after_click(env, monkeypatch):
    info = {"channel_id": "UCbbbbbbbbbbbbbbbbbbbbbb", "title": "Rival", "handle": "@rival", "uploads_playlist": "UUb",
            "thumb_url": "", "video_count": 5}
    monkeypatch.setattr(upl, "preview_binding", lambda *a, **k: {
        "kind": "handle", "channel": info, "name_matches": False, "video_count": 5,
        "video_title": None, "video_title_matches": None, "bound_to": None})
    at = run_page()
    at.text_input(key=f"bind_url_{SLUG}").input("@rival").run()
    next(b for b in at.button if b.key == f"bind_go_{SLUG}").click().run()
    assert any("KHÁC" in e.value for e in at.error)
    assert repo.get_channel(env["db"], SLUG) is None
    next(b for b in at.button if b.key == f"bind_ok_{SLUG}").click().run()
    assert repo.get_channel(env["db"], SLUG)["channel_id"] == info["channel_id"]


def test_strong_proposal_shown_and_rejectable(env):
    bind(env["db"])
    repo.replace_remote(env["db"], CID, [{"video_id": "AAAAAAAAAAA", "title": TITLE, "published_at": "2026-11-02T08:40:42Z"}])
    at = open_card(run_page(), 1)
    assert any("Đề xuất mạnh" in m.value for m in at.markdown)
    next(b for b in at.button if (b.key or "").startswith(f"rej_{SLUG}_1_")).click().run()
    assert (SLUG, 1, "AAAAAAAAAAA") in repo.rejected_pairs(env["db"])
    assert not any("Đề xuất mạnh" in m.value for m in at.markdown)


def test_old_video_shows_meta_file_verbatim(env):
    text = "# YouTube kit — Hello World\n\n## Title\n1. Hello World\n"
    w(env["root"] / SLUG / "video-5-hello-world" / "YOUTUBE.md", text)
    at = run_page()
    next(b for b in at.button if b.key == f"open_{SLUG}_5").click().run()
    assert text in [c.value for c in at.code]
