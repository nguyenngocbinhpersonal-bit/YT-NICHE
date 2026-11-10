"""Gom video đã upload theo ngày: mỗi video một lần, ngày thật thắng ngày ước tính, ngày theo giờ VN."""

from datetime import date, timedelta, timezone
from types import SimpleNamespace

from yt_niche.core import upload_history as uh
from yt_niche.core import upload_match as um
from yt_niche.core import upload_state as us

VN = timezone(timedelta(hours=7))
SLUG = "chan"


def vid(number, *, kind="pipeline", state="complete", published_at=None, url=None, video_id=None, title="T"):
    return SimpleNamespace(
        channel_slug=SLUG, number=number, kind=kind, state=state, published_at=published_at, youtube_url=url,
        youtube_video_id=video_id, title=title, titles=[], slug_title="", name=f"folder-{number}",
    )


def remote(video_id, published_at="2026-11-05T10:00:00Z", title="R"):
    return um.RemoteVideo(video_id, "UC", title, published_at)


def strong(number, video_id):
    return SimpleNamespace(channel_slug=SLUG, number=number, video_id=video_id, strength=um.STRONG)


def test_video_not_uploaded_is_left_out():
    assert uh.channel_entries(SLUG, [vid(1)], {}, [], []) == []


def test_published_with_link_is_confirmed_and_shown_once():
    v = vid(1, state="published", published_at="2026-11-02T08:40:42Z", url="https://youtu.be/a", video_id="a")
    out = uh.channel_entries(SLUG, [v], {}, [remote("a")], [])
    assert len(out) == 1
    assert (out[0].source, out[0].published_at, out[0].number) == (uh.CONFIRMED, "2026-11-02T08:40:42Z", 1)


def test_linked_video_shows_the_real_title_from_the_channel():
    old = vid(8, kind="old", state=None, title=None)
    old.titles = ["Phương án local"]
    st = {"video_url": "https://youtu.be/a", "video_id": "a", "published_at": "2026-10-25T08:28:10Z"}
    out = uh.channel_entries(SLUG, [old], {8: st}, [remote("a", title="Tiêu đề thật")], [])
    assert out[0].title == "Tiêu đề thật"


def test_facebook_mark_without_link_is_marked():
    v = vid(1, state="published", published_at="2026-11-02T08:00:00Z")
    assert uh.channel_entries(SLUG, [v], {}, [], [])[0].source == uh.MARKED


def test_facebook_link_from_db_is_used():
    v = vid(1, state="published", published_at="2026-11-02T08:00:00Z")
    out = uh.channel_entries(SLUG, [v], {1: {"fb_url": "https://facebook.com/x/videos/1"}}, [], [])
    assert (out[0].source, out[0].url) == (uh.CONFIRMED, "https://facebook.com/x/videos/1")


def test_waiting_uses_due_time():
    st = {"manual_state": us.MANUAL_WAITING_UPLOAD, "due_at": "2026-11-07T06:00:00Z"}
    out = uh.channel_entries(SLUG, [vid(1)], {1: st}, [], [])
    assert (out[0].source, out[0].published_at) == (uh.WAITING, "2026-11-07T06:00:00Z")


def test_real_youtube_date_beats_estimated_mark_date():
    v = vid(1, state="published", published_at="2026-11-07T06:00:00Z")
    out = uh.channel_entries(SLUG, [v], {}, [remote("a", "2026-11-07T05:11:00Z")], [strong(1, "a")])
    assert len(out) == 1
    assert (out[0].source, out[0].published_at, out[0].video_id) == (uh.UNCONFIRMED, "2026-11-07T05:11:00Z", "a")


def test_remote_with_strong_match_but_unconfirmed_keeps_the_number():
    out = uh.channel_entries(SLUG, [vid(1)], {}, [remote("a")], [strong(1, "a")])
    assert (out[0].source, out[0].number) == (uh.UNCONFIRMED, 1)


def test_remote_without_folder_is_listed():
    out = uh.channel_entries(SLUG, [], {}, [remote("a")], [])
    assert (out[0].source, out[0].number) == (uh.NO_FOLDER, None)


def test_weak_match_does_not_claim_a_folder():
    weak = SimpleNamespace(channel_slug=SLUG, number=1, video_id="a", strength=um.WEAK)
    assert uh.channel_entries(SLUG, [vid(1)], {}, [remote("a")], [weak])[0].source == uh.NO_FOLDER


def test_old_video_only_when_confirmed_or_facebook_marked():
    old = vid(5, kind="old", state=None)
    assert uh.channel_entries(SLUG, [old], {}, [], []) == []
    st = {"manual_state": us.MANUAL_FB_DONE, "published_at": "2026-10-01T05:00:00Z"}
    assert uh.channel_entries(SLUG, [old], {5: st}, [], [])[0].source == uh.MARKED


def test_day_boundary_is_vietnam_time():
    assert uh.local_day("2026-11-06T23:06:05Z", VN) == date(2026, 11, 7)
    assert uh.local_day("2026-11-06T16:59:59Z", VN) == date(2026, 11, 6)
    assert uh.local_day("sai", VN) is None


def e(number, at):
    return uh.Entry(SLUG, number, "T", at, "", uh.MARKED)


def test_by_day_newest_first_and_unknown_last():
    days = uh.by_day([e(1, "2026-11-01T01:00:00Z"), e(2, None), e(3, "2026-11-05T01:00:00Z"), e(4, "2026-11-05T09:00:00Z")], VN)
    assert [d for d, _ in days] == [date(2026, 11, 5), date(2026, 11, 1), None]
    assert [x.number for x in days[0][1]] == [4, 3]


def test_within_days_counts_today_and_keeps_unknown():
    today = date(2026, 11, 7)
    items = [e(1, "2026-11-01T01:00:00Z"), e(2, "2026-10-31T01:00:00Z"), e(3, None)]
    assert [x.number for x in uh.within_days(items, 7, today, VN)] == [1, 3]
    assert len(uh.within_days(items, None, today, VN)) == 3


def test_day_label():
    today = date(2026, 11, 7)  # thứ Bảy
    assert uh.day_label(today, today) == "Hôm nay · T7, 07/11/2026"
    assert uh.day_label(date(2026, 11, 6), today) == "Hôm qua · T6, 06/11/2026"
    assert uh.day_label(date(2026, 11, 1), today) == "CN, 01/11/2026"
    assert uh.day_label(None, today) == "Chưa rõ ngày"


def test_escape_md_keeps_special_characters_literal():
    assert uh.escape_md("$5 [a]: *b*") == r"\$5 \[a\]\: \*b\*"
