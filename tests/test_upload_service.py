"""Trang Upload: bảng DB, đọc YouTube (API giả), gán kênh, làm mới, kiểm URL video."""

import httpx
import pytest

from yt_niche.db import uploads as repo
from yt_niche.services import uploads as svc
from yt_niche.sources.youtube import QuotaExceeded, YouTubeClient, YouTubeError

UC_A = "UC" + "A" * 22
UC_B = "UC" + "B" * 22
VID_A = "AAAAAAAAAAA"
VID_B = "BBBBBBBBBBB"
VID_GONE = "GGGGGGGGGGG"

CHANNELS = {
    UC_A: {"title": "Stillhouse Stories", "customUrl": "@stillhouse-stories", "uploads": "UUA", "videos": 2},
    UC_B: {"title": "Rival Channel", "customUrl": "@rival", "uploads": "UUB", "videos": 1},
}
HANDLES = {"@stillhouse-stories": UC_A, "@rival": UC_B}
VIDEOS = {
    VID_A: {"channel": UC_A, "title": "The Last Lighthouse", "privacy": "public", "published": "2026-11-02T08:40:11Z"},
    VID_B: {"channel": UC_B, "title": "Rival Video", "privacy": "public", "published": "2026-10-01T00:00:00Z"},
}


def _channel_item(cid):
    c = CHANNELS[cid]
    return {
        "id": cid,
        "snippet": {"title": c["title"], "customUrl": c["customUrl"], "thumbnails": {"default": {"url": "http://t/x.jpg"}}},
        "contentDetails": {"relatedPlaylists": {"uploads": c["uploads"]}},
        "statistics": {"videoCount": str(c["videos"])},
    }


def _video_item(vid):
    v = VIDEOS[vid]
    return {
        "id": vid,
        "snippet": {"channelId": v["channel"], "title": v["title"], "publishedAt": v["published"]},
        "status": {"privacyStatus": v["privacy"]},
        "contentDetails": {"duration": "PT10M"},
    }


def make_api(*, pages=None, fail_playlist=None, log=None):
    """pages: {playlist_id: [page_items,...]} phân trang theo pageToken '1', '2'..."""
    pages = pages or {
        "UUA": [[("a1aaaaaaaaa", "Episode One", "2026-10-20T10:00:00Z", "2026-11-03T00:00:00Z")]],
        "UUB": [[("b1bbbbbbbbb", "Rival Video", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")]],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        ep = request.url.path.rsplit("/", 1)[-1]
        if log is not None:
            log.append((ep, dict(p)))
        if ep == "channels":
            if "forHandle" in p:
                cid = HANDLES.get(p["forHandle"])
                return httpx.Response(200, json={"items": [_channel_item(cid)] if cid else []})
            return httpx.Response(200, json={"items": [_channel_item(c) for c in p["id"].split(",") if c in CHANNELS]})
        if ep == "videos":
            return httpx.Response(200, json={"items": [_video_item(v) for v in p["id"].split(",") if v in VIDEOS]})
        if ep == "playlistItems":
            pl = p["playlistId"]
            if fail_playlist and pl in fail_playlist:
                code, reason = fail_playlist[pl]
                return httpx.Response(code, json={"error": {"message": "boom", "errors": [{"reason": reason}]}})
            plist = pages.get(pl)
            if plist is None:
                return httpx.Response(404, json={"error": {"message": "nf", "errors": [{"reason": "playlistNotFound"}]}})
            idx = int(p.get("pageToken", "0"))
            body = {
                "items": [
                    {
                        "snippet": {"title": t, "publishedAt": added, "resourceId": {"videoId": vid}},
                        "contentDetails": {"videoId": vid, "videoPublishedAt": pub},
                    }
                    for vid, t, pub, added in plist[idx]
                ]
            }
            if idx + 1 < len(plist):
                body["nextPageToken"] = str(idx + 1)
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def info(cid):
    c = CHANNELS[cid]
    return {"channel_id": cid, "title": c["title"], "handle": c["customUrl"], "uploads_playlist": c["uploads"], "thumb_url": ""}


# ---------------------------------------------------------------------------
# sources/youtube.py
# ---------------------------------------------------------------------------


async def test_upload_items_paginates_and_uses_video_published_at(conn):
    pages = {"UUA": [
        [(f"p0v{i:08d}", f"T{i}", f"2026-10-{i + 1:02d}T00:00:00Z", "2026-11-05T00:00:00Z") for i in range(3)],
        [("p1v00000000", "T9", "2026-09-01T00:00:00Z", "2026-11-06T00:00:00Z")],
    ]}
    log = []
    async with YouTubeClient(conn, api_key="k", transport=make_api(pages=pages, log=log)) as yt:
        items = await yt.upload_items("UUA")
    assert [i["video_id"] for i in items] == ["p0v00000000", "p0v00000001", "p0v00000002", "p1v00000000"]
    assert items[0]["published_at"] == "2026-10-01T00:00:00Z"  # videoPublishedAt, không phải ngày thêm vào playlist
    assert [c[1].get("pageToken") for c in log] == [None, "1"]
    assert log[0][1]["part"] == "snippet,contentDetails"


async def test_upload_items_does_not_use_cache(conn):
    log = []
    async with YouTubeClient(conn, api_key="k", transport=make_api(log=log)) as yt:
        await yt.upload_items("UUA")
        await yt.upload_items("UUA")
    assert len(log) == 2


async def test_upload_items_api_error_is_raised_not_empty_list(conn):
    t = make_api(fail_playlist={"UUA": (500, "backendError")})
    async with YouTubeClient(conn, api_key="k", transport=t) as yt:
        with pytest.raises(YouTubeError):
            await yt.upload_items("UUA")


async def test_upload_items_quota_error_raises_quota_exceeded(conn):
    t = make_api(fail_playlist={"UUA": (403, "quotaExceeded")})
    async with YouTubeClient(conn, api_key="k", transport=t) as yt:
        with pytest.raises(QuotaExceeded):
            await yt.upload_items("UUA")


async def test_upload_items_playlist_not_found_is_empty(conn):
    async with YouTubeClient(conn, api_key="k", transport=make_api()) as yt:
        assert await yt.upload_items("UUmissing") == []


async def test_video_status_and_channel_lookup(conn):
    async with YouTubeClient(conn, api_key="k", transport=make_api()) as yt:
        v = (await yt.video_status([VID_A]))[0]
        assert v["snippet"]["channelId"] == UC_A and v["status"]["privacyStatus"] == "public"
        by_handle = await yt.channel_lookup(handle="@stillhouse-stories")
        by_id = await yt.channel_lookup(channel_id=UC_B)
        assert await yt.channel_lookup(handle="@nobody") is None
    assert by_handle["channel_id"] == UC_A and by_handle["uploads_playlist"] == "UUA" and by_handle["video_count"] == 2
    assert by_id["title"] == "Rival Channel"


# ---------------------------------------------------------------------------
# preview / bind / rebind
# ---------------------------------------------------------------------------


def test_preview_by_handle_and_name_match(conn):
    r = svc.preview_binding(conn, "stillhouse", "https://www.youtube.com/@stillhouse-stories", "Stillhouse Stories", [],
                            api_key="k", transport=make_api())
    assert r["kind"] == "handle" and r["channel"]["channel_id"] == UC_A
    assert r["name_matches"] is True and r["video_count"] == 2 and r["video_title_matches"] is None
    assert repo.get_channel(conn, "stillhouse") is None  # chưa ghi gì


def test_preview_name_mismatch_is_reported(conn):
    r = svc.preview_binding(conn, "x", "@rival", "Stillhouse Stories", [], api_key="k", transport=make_api())
    assert r["name_matches"] is False


def test_preview_by_channel_url(conn):
    r = svc.preview_binding(conn, "x", f"https://www.youtube.com/channel/{UC_B}", "Rival Channel", [],
                            api_key="k", transport=make_api())
    assert r["channel"]["channel_id"] == UC_B and r["name_matches"]


def test_preview_by_video_url_reads_channel_and_matches_title(conn):
    url = f"https://www.youtube.com/watch?v={VID_A}"
    r = svc.preview_binding(conn, "stillhouse", url, "Stillhouse Stories", ["Other", "the last LIGHTHOUSE"],
                            api_key="k", transport=make_api())
    assert r["kind"] == "video" and r["channel"]["channel_id"] == UC_A
    assert r["video_title"] == "The Last Lighthouse" and r["video_title_matches"] is True
    r2 = svc.preview_binding(conn, "stillhouse", url, "Stillhouse Stories", ["Nope"], api_key="k", transport=make_api())
    assert r2["video_title_matches"] is False


def test_preview_unknown_channel_and_video_raise(conn):
    with pytest.raises(svc.UploadError):
        svc.preview_binding(conn, "x", "@nobody", "N", [], api_key="k", transport=make_api())
    with pytest.raises(svc.UploadError):
        svc.preview_binding(conn, "x", f"https://youtu.be/{VID_GONE}", "N", [], api_key="k", transport=make_api())


def test_bind_and_duplicate_binding_blocked(conn):
    svc.bind(conn, "stillhouse", info(UC_A))
    assert repo.get_channel(conn, "stillhouse")["channel_id"] == UC_A
    with pytest.raises(svc.UploadError, match="stillhouse"):
        svc.bind(conn, "other", info(UC_A))
    assert repo.get_channel(conn, "other") is None
    r = svc.preview_binding(conn, "other", "@stillhouse-stories", "Stillhouse Stories", [], api_key="k", transport=make_api())
    assert r["bound_to"] == "stillhouse"


def test_rebind_changes_channel_and_drops_old_snapshot(conn):
    svc.bind(conn, "s", info(UC_A))
    repo.replace_remote(conn, UC_A, [{"video_id": "x", "title": "t", "published_at": None}])
    svc.rebind(conn, "s", info(UC_B))
    assert repo.get_channel(conn, "s")["channel_id"] == UC_B
    assert repo.list_remote(conn, UC_A) == []
    svc.bind(conn, "t", info(UC_A))  # UC_A đã rảnh


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_stores_snapshot_and_timestamp(conn):
    svc.bind(conn, "s", info(UC_A))
    res = svc.refresh(conn, ["s"], api_key="k", transport=make_api())
    assert res.counts == {"s": 1} and res.errors == {}
    rows = repo.list_remote(conn, UC_A)
    assert rows[0]["video_id"] == "a1aaaaaaaaa" and rows[0]["published_at"] == "2026-10-20T10:00:00Z"
    ch = repo.get_channel(conn, "s")
    assert ch["last_refresh_at"] and ch["last_error"] == ""


def test_refresh_replaces_snapshot(conn):
    svc.bind(conn, "s", info(UC_A))
    repo.replace_remote(conn, UC_A, [{"video_id": "old", "title": "old", "published_at": None}])
    svc.refresh(conn, ["s"], api_key="k", transport=make_api())
    assert [r["video_id"] for r in repo.list_remote(conn, UC_A)] == ["a1aaaaaaaaa"]


def test_refresh_error_on_one_channel_does_not_break_others(conn):
    svc.bind(conn, "a", info(UC_A))
    svc.bind(conn, "b", info(UC_B))
    res = svc.refresh(conn, ["a", "b"], api_key="k", transport=make_api(fail_playlist={"UUA": (500, "backendError")}))
    assert "a" in res.errors and res.counts == {"b": 1} and not res.quota_exceeded
    assert repo.get_channel(conn, "a")["last_error"]
    assert repo.get_channel(conn, "a")["last_refresh_at"] is None
    assert repo.list_remote(conn, UC_B)


def test_refresh_failure_keeps_previous_snapshot(conn):
    svc.bind(conn, "a", info(UC_A))
    repo.replace_remote(conn, UC_A, [{"video_id": "keep", "title": "t", "published_at": None}])
    svc.refresh(conn, ["a"], api_key="k", transport=make_api(fail_playlist={"UUA": (500, "backendError")}))
    assert [r["video_id"] for r in repo.list_remote(conn, UC_A)] == ["keep"]


def test_refresh_quota_stops_and_reports(conn):
    svc.bind(conn, "a", info(UC_A))
    svc.bind(conn, "b", info(UC_B))
    log = []
    res = svc.refresh(conn, ["a", "b"], api_key="k",
                      transport=make_api(fail_playlist={"UUA": (403, "quotaExceeded")}, log=log))
    assert res.quota_exceeded and "a" in res.errors and "b" not in res.errors and res.counts == {}
    assert [c[1]["playlistId"] for c in log] == ["UUA"]  # không gọi tiếp kênh sau


def test_refresh_ignores_unbound_slug(conn):
    assert svc.refresh(conn, ["nobody"], api_key="k", transport=make_api()).counts == {}


# ---------------------------------------------------------------------------
# check_video_url
# ---------------------------------------------------------------------------


def test_check_video_ok(conn):
    svc.bind(conn, "s", info(UC_A))
    r = svc.check_video_url(conn, "s", f"https://youtu.be/{VID_A}", api_key="k", transport=make_api())
    assert r["status"] == "ok" and r["privacy"] == "public" and r["published_at"] == "2026-11-02T08:40:11Z"
    assert r["video_id"] == VID_A


def test_check_video_other_channel_is_mismatch(conn):
    svc.bind(conn, "s", info(UC_A))
    svc.bind(conn, "rival", info(UC_B))
    r = svc.check_video_url(conn, "s", f"https://www.youtube.com/watch?v={VID_B}", api_key="k", transport=make_api())
    assert r["status"] == "channel_mismatch" and r["other_slug"] == "rival"


def test_check_video_not_readable(conn):
    svc.bind(conn, "s", info(UC_A))
    r = svc.check_video_url(conn, "s", f"https://youtu.be/{VID_GONE}", api_key="k", transport=make_api())
    assert r["status"] == "not_readable" and r["published_at"] is None


def test_check_video_channel_unbound_makes_no_api_call(conn):
    log = []
    r = svc.check_video_url(conn, "s", f"https://youtu.be/{VID_A}", api_key="k", transport=make_api(log=log))
    assert r["status"] == "channel_unbound" and log == []


def test_check_video_api_error_propagates(conn):
    svc.bind(conn, "s", info(UC_A))

    def boom(request):
        return httpx.Response(500, json={"error": {"message": "x", "errors": [{"reason": "backendError"}]}})

    with pytest.raises(YouTubeError):
        svc.check_video_url(conn, "s", f"https://youtu.be/{VID_A}", api_key="k", transport=httpx.MockTransport(boom))


def test_check_video_rejects_non_video_url(conn):
    svc.bind(conn, "s", info(UC_A))
    with pytest.raises(svc.UploadError):
        svc.check_video_url(conn, "s", "@stillhouse-stories", api_key="k", transport=make_api())


# ---------------------------------------------------------------------------
# repo: trạng thái video, từ chối đề xuất
# ---------------------------------------------------------------------------


def test_video_state_roundtrip_and_thumb_limit(conn):
    assert repo.get_state(conn, "s", 7) is None
    repo.update_state(conn, "s", 7, thumb_choice=["a.png", "thumbnail/b.png"], mp4_choice="v.mp4")
    repo.update_state(conn, "s", 7, manual_state="waiting_public")
    st = repo.get_state(conn, "s", 7)
    assert st["thumb_choice"] == ["a.png", "thumbnail/b.png"] and st["mp4_choice"] == "v.mp4"
    assert st["manual_state"] == "waiting_public" and st["catalog_pending"] == 0
    with pytest.raises(ValueError):
        repo.update_state(conn, "s", 7, thumb_choice=["1", "2", "3", "4"])
    with pytest.raises(ValueError):
        repo.update_state(conn, "s", 7, bogus=1)


def test_reject_match_persists_and_can_be_undone(conn):
    repo.reject_match(conn, "s", 7, "v1")
    repo.reject_match(conn, "s", 7, "v1")  # idempotent
    assert repo.rejected_pairs(conn, "s") == {("s", 7, "v1")}
    repo.unreject_match(conn, "s", 7, "v1")
    assert repo.rejected_pairs(conn) == set()


def test_studio_link_is_read_as_video_by_both_functions(conn):
    url = f"https://studio.youtube.com/video/{VID_A}/edit"
    assert svc.parse_url(url) == ("video", VID_A)
    r = svc.preview_binding(conn, "s", url, "Stillhouse Stories", [], api_key="k", transport=make_api())
    assert r["kind"] == "video"
    svc.bind(conn, "s", r["channel"])
    c = svc.check_video_url(conn, "s", url, api_key="k", transport=make_api())
    assert c["video_id"] == VID_A


def test_bad_url_raises_upload_error_in_both_functions(conn):
    with pytest.raises(svc.UploadError):
        svc.preview_binding(conn, "s", "không phải url", "N", [], api_key="k", transport=make_api())
    with pytest.raises(svc.UploadError):
        svc.check_video_url(conn, "s", "https://example.com/", api_key="k", transport=make_api())
