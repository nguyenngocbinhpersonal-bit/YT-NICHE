"""Kênh Facebook (T6): link, ngày, trạng thái, nền tảng theo kênh. Không gọi API, không đụng folder thật."""

from datetime import date, datetime, timezone

import pytest

from yt_niche import config as cfg
from yt_niche.core import upload_facebook as fb
from yt_niche.core import upload_state as us
from yt_niche.db import uploads as repo
from yt_niche.db.connection import connect
from yt_niche.services import uploads as svc

PAGE = "https://www.facebook.com/CoNhanChiThuat/"
NOW = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)  # 10:00 giờ VN


# ---------------------------------------------------------------------------
# parse_facebook_url
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.facebook.com/CoNhanChiThuat/videos/1234567890",
    "https://facebook.com/CoNhanChiThuat/videos/some-title/1234567890/",
    "https://m.facebook.com/CoNhanChiThuat/posts/987654321",
    "https://web.facebook.com/reel/1234567890",
    "https://www.facebook.com/watch?v=1234567890",
    "https://www.facebook.com/watch/?v=1234567890",
    "https://www.facebook.com/share/v/AbCdEf/",
    "https://fb.watch/abcDEF/",
    "https://fb.com/CoNhanChiThuat/videos/12345",
    "facebook.com/CoNhanChiThuat/videos/12345",
])
def test_accepts_video_links(url):
    r = fb.parse_facebook_url(url, PAGE)
    assert r.ok and not r.error and not r.red


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/CoNhanChiThuat/",  # chỉ tới trang
    "https://www.facebook.com/CoNhanChiThuat",
    "https://www.facebook.com/",
    "https://www.facebook.com/watch",
    "https://www.youtube.com/watch?v=AAAAAAAAAAA",
    "https://youtu.be/AAAAAAAAAAA",
    "https://facebook.com.evil.com/CoNhanChiThuat/videos/1",
    "https://evilfacebook.com/CoNhanChiThuat/videos/1",
    "https://fb.watch/",
    "ftp://www.facebook.com/a/videos/1",
    "",
])
def test_rejects_non_video_or_wrong_host(url):
    r = fb.parse_facebook_url(url, PAGE)
    assert not r.ok and r.error


def test_unknown_shape_is_yellow_not_rejected():
    r = fb.parse_facebook_url("https://www.facebook.com/permalink.php?story_fbid=1&id=2", PAGE)
    assert r.ok and r.warnings


def test_other_page_is_red_but_not_blocked():
    r = fb.parse_facebook_url("https://www.facebook.com/KhacPage/videos/123", PAGE)
    assert r.ok and r.red and "khác" in r.red[0]


def test_no_page_url_no_red():
    assert not fb.parse_facebook_url("https://www.facebook.com/KhacPage/videos/123", "").red


def test_link_key_equates_variants():
    a = fb.link_key("https://www.facebook.com/Page/videos/123/")
    assert a == fb.link_key("https://m.facebook.com/page/videos/123")
    assert fb.link_key("https://www.facebook.com/watch?v=1") != fb.link_key("https://www.facebook.com/watch?v=2")


# ---------------------------------------------------------------------------
# Ngày
# ---------------------------------------------------------------------------
def test_date_is_noon_vietnam_time():
    assert fb.date_to_iso(date(2026, 11, 5), NOW) == "2026-11-05T05:00:00Z"


def test_future_date_rejected_today_ok():
    assert fb.date_to_iso(date(2026, 11, 7), NOW).startswith("2026-11-07")
    with pytest.raises(ValueError):
        fb.date_to_iso(date(2026, 11, 8), NOW)


def test_vn_date_boundary():
    late_utc = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)  # đã sang 08/11 giờ VN
    assert fb.date_to_iso(date(2026, 11, 8), late_utc).startswith("2026-11-08")


# ---------------------------------------------------------------------------
# Trạng thái
# ---------------------------------------------------------------------------
def test_order_includes_cho_ghi_in_todo():
    assert us.CHO_GHI in us.TODO_ORDER and us.CHO_GHI not in (us.XONG,)


def test_facebook_published_is_xong_without_link():
    assert us.video_status(kind="pipeline", run_state="published", platform=us.FACEBOOK) == us.XONG


def test_youtube_published_without_link_is_not_xong_even_after_mark():
    assert us.video_status(kind="pipeline", run_state="published", platform=us.YOUTUBE) == us.DA_DANG_CHUA_LINK
    assert us.video_status(kind="pipeline", run_state="published", has_link=True) == us.XONG


def test_waiting_status_and_precedence():
    w = us.MANUAL_WAITING_UPLOAD
    assert us.video_status(kind="pipeline", run_state="complete", manual_state=w) == us.CHO_GHI
    assert us.video_status(kind="pipeline", run_state="complete", manual_state=w, platform=us.FACEBOOK) == us.CHO_GHI
    # can_xac_nhan thắng cho_ghi; cho_ghi thắng da_dang_chua_link; ghi_thieu và dang_san_xuat thắng tất cả
    assert us.video_status(kind="pipeline", run_state="complete", manual_state=w, has_proposal=True) == us.CAN_XAC_NHAN
    assert us.video_status(kind="pipeline", run_state="published", manual_state=w) == us.CHO_GHI
    assert us.video_status(kind="pipeline", run_state="published", manual_state=w, catalog_pending=True) == us.GHI_THIEU
    assert us.video_status(kind="pipeline", run_state="in_flight", manual_state=w) == us.DANG_SAN_XUAT


def test_facebook_ignores_proposals_and_old_marked_is_xong():
    assert us.video_status(kind="pipeline", run_state="complete", has_proposal=True, platform=us.FACEBOOK) == us.SAN_SANG
    assert us.video_status(kind="old", manual_state=us.MANUAL_FB_DONE, platform=us.FACEBOOK) == us.XONG
    assert us.video_status(kind="old", platform=us.FACEBOOK) == us.CHUA_DOI_CHIEU


def test_can_mark_later_statuses():
    for s in (us.SAN_SANG, us.THIEU_DO, us.CHO_PUBLIC, us.CAN_XAC_NHAN):
        assert us.can_mark_later(s, "pipeline")
    for s in (us.XONG, us.CHO_GHI, us.DA_DANG_CHUA_LINK, us.GHI_THIEU, us.DANG_SAN_XUAT):
        assert not us.can_mark_later(s, "pipeline")
    assert not us.can_mark_later(us.SAN_SANG, "old")


def test_unlinked_warning_exact_24h_boundary():
    marked = "2026-11-06T03:00:00Z"
    assert not us.unlinked_overdue(marked, datetime(2026, 11, 7, 2, 59, 59, tzinfo=timezone.utc))
    assert us.unlinked_overdue(marked, datetime(2026, 11, 7, 3, 0, 0, tzinfo=timezone.utc))
    assert not us.unlinked_overdue(None, NOW)


# ---------------------------------------------------------------------------
# Nền tảng theo kênh
# ---------------------------------------------------------------------------
@pytest.fixture
def db(tmp_path):
    return connect(tmp_path / "a.db")


def test_defaults_from_config_and_unknown_is_youtube(db):
    assert svc.platform_of(db, "nhu-vay-toi-nghe") == us.FACEBOOK
    assert svc.platform_of(db, "thau") == us.FACEBOOK
    assert svc.platform_of(db, "amateur-filmmaker") == us.YOUTUBE
    assert svc.page_url_of(db, "nhu-vay-toi-nghe") == cfg.UPLOAD_FACEBOOK_CHANNELS["nhu-vay-toi-nghe"]
    assert svc.page_url_of(db, "thau") == ""


def test_db_row_wins_and_validates_page_url(db):
    svc.set_page_url(db, "thau", "https://www.facebook.com/ThauPage/")
    assert svc.page_url_of(db, "thau") == "https://www.facebook.com/ThauPage/"
    assert svc.platform_of(db, "thau") == us.FACEBOOK
    with pytest.raises(svc.UploadError):
        svc.set_page_url(db, "thau", "https://www.youtube.com/@thau")
    with pytest.raises(svc.UploadError):
        svc.set_page_url(db, "amateur-filmmaker", "https://www.facebook.com/X/")  # kênh YouTube
    svc.set_page_url(db, "thau", "")
    assert svc.page_url_of(db, "thau") == ""


def test_facebook_channel_cannot_bind_and_is_skipped_by_refresh(db):
    info = {"channel_id": "UC" + "x" * 22, "title": "T", "handle": "@t", "uploads_playlist": "UUx", "thumb_url": ""}
    with pytest.raises(svc.UploadError):
        svc.bind(db, "thau", info)
    repo.upsert_channel(db, "thau", info)  # dòng lạc từ trước
    import httpx

    seen = []
    t = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(500))
    res = svc.refresh(db, ["thau"], api_key="k", transport=t)
    assert not seen and not res.errors and not res.counts


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE upload_video_state (channel_slug TEXT NOT NULL, number INTEGER NOT NULL, manual_state TEXT, "
              "video_url TEXT, video_id TEXT, published_at TEXT, thumb_choice TEXT NOT NULL DEFAULT '[]', mp4_choice TEXT, "
              "catalog_pending INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY (channel_slug, number))")
    c.commit()
    c.close()
    conn = connect(p)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(upload_video_state)")}
    assert {"due_at", "fb_url"} <= cols
