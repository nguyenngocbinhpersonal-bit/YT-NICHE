import httpx
import pytest

from yt_niche.sources.youtube import QuotaExceeded, YouTubeClient


def _video(vid, duration, views, live="none"):
    return {
        "id": vid,
        "snippet": {"channelId": "C1", "title": vid, "liveBroadcastContent": live},
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": duration},
    }


CHANNEL = {
    "id": "C1",
    "snippet": {"title": "Channel 1", "publishedAt": "2025-01-01T00:00:00Z"},
    "statistics": {"subscriberCount": "5000", "videoCount": "6", "hiddenSubscriberCount": False},
    "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
}

UPLOADS = [
    _video("L1", "PT10M", 1000),
    _video("L2", "PT8M", 3000),
    _video("L3", "PT12M", 2000),
    _video("S1", "PT30S", 100),
    _video("S2", "PT59S", 300),
    _video("LIVE", "P0D", 99999, live="live"),
]


def make_transport(counter):
    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        counter[endpoint] = counter.get(endpoint, 0) + 1
        if endpoint == "channels":
            return httpx.Response(200, json={"items": [CHANNEL]})
        if endpoint == "playlistItems":
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v["id"]}} for v in UPLOADS]})
        if endpoint == "videos":
            ids = request.url.params["id"].split(",")
            return httpx.Response(200, json={"items": [v for v in UPLOADS if v["id"] in ids]})
        if endpoint == "search":
            return httpx.Response(
                403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_videos_cached_on_second_call(conn):
    hits = {}
    async with YouTubeClient(conn, api_key="k", transport=make_transport(hits)) as yt:
        await yt.videos(["L1", "L2"])
        await yt.videos(["L1", "L2"])
        assert hits["videos"] == 1
        assert yt.meter.units["videos"] == 1
        assert yt.meter.cache_hits["videos"] == 1


async def test_channel_medians_split_by_format_and_cached(conn):
    hits = {}
    async with YouTubeClient(conn, api_key="k", transport=make_transport(hits)) as yt:
        channels = await yt.channels(["C1"])
        rows = await yt.channel_medians(channels)
        row = rows["C1"]
        assert row["median_long"] == 2000 and row["n_long"] == 3
        assert row["median_short"] == 200 and row["n_short"] == 2  # livestreams are skipped
        assert row["subs"] == 5000

        await yt.channel_medians(channels)  # 2nd call: served from the channel table
        assert hits["playlistItems"] == 1
        assert yt.meter.cache_hits["channel_median"] == 1


async def test_quota_exceeded_raises_and_is_logged(conn):
    async with YouTubeClient(conn, api_key="k", transport=make_transport({})) as yt:
        with pytest.raises(QuotaExceeded):
            await yt.search_videos("x", region_code="VN", language="vi", published_after="2026-01-01T00:00:00Z")
    assert conn.execute("SELECT SUM(units) FROM quota_log").fetchone()[0] == 100
