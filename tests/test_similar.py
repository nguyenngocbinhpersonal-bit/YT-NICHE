"""Video & kênh tương tự: đọc URL, bộ đọc innertube, chấm điểm, và cả luồng với API giả."""

import json

import httpx
import pytest

from yt_niche.core.similar import Corpus, rank_channels, rank_videos
from yt_niche.services import similar
from yt_niche.sources import innertube as itb

# ---------------------------------------------------------------------------
# Đọc đầu vào
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.youtube.com/watch?v=W_Wbor3YdFw", ("video", "W_Wbor3YdFw")),
        ("https://www.youtube.com/watch?list=PL1&v=W_Wbor3YdFw&t=10", ("video", "W_Wbor3YdFw")),
        ("https://youtu.be/W_Wbor3YdFw?si=x", ("video", "W_Wbor3YdFw")),
        ("https://www.youtube.com/shorts/W_Wbor3YdFw", ("video", "W_Wbor3YdFw")),
        ("W_Wbor3YdFw", ("video", "W_Wbor3YdFw")),
        ("https://www.youtube.com/channel/UCNK7wpb_UVN0GSeGLcIHdNg/videos", ("channel", "UCNK7wpb_UVN0GSeGLcIHdNg")),
        ("https://www.youtube.com/@quasepossivel/videos", ("handle", "@quasepossivel")),
        ("https://www.youtube.com/@k%C3%AAnhvi%E1%BB%87t", ("handle", "@kênhviệt")),
        ("@quasepossivel", ("handle", "@quasepossivel")),
    ],
)
def test_parse_input(url, expected):
    assert similar.parse_input(url) == expected


def test_parse_input_rejects_other_links():
    with pytest.raises(similar.SimilarError):
        similar.parse_input("https://example.com/abc")


@pytest.mark.parametrize(
    "s, n", [("151,902 views", 151902), ("16K", 16000), ("1.2M views", 1200000), ("No views", None), ("", None)]
)
def test_parse_count(s, n):
    assert itb.parse_count(s) == n


def test_channel_tags_keeps_quoted_phrases():
    assert similar.channel_tags('animação "e se" ciência') == ["animação", "e se", "ciência"]


# ---------------------------------------------------------------------------
# Dữ liệu giả cho innertube
# ---------------------------------------------------------------------------


def lockup(vid, title, channel, cid, views="16K"):
    return {
        "lockupViewModel": {
            "contentId": vid,
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": title},
                    "image": {"decoratedAvatarViewModel": {"x": {"browseEndpoint": {"browseId": cid}}}},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {"metadataParts": [{"text": {"content": channel}}]},
                                {"metadataParts": [{"text": {"content": views}}, {"text": {"content": "1 day ago"}}]},
                            ]
                        }
                    },
                }
            },
        }
    }


def video_renderer(vid, title, channel, cid, views="1,000 views"):
    return {
        "videoRenderer": {
            "videoId": vid,
            "title": {"runs": [{"text": title}]},
            "ownerText": {"runs": [{"text": channel, "navigationEndpoint": {"browseEndpoint": {"browseId": cid}}}]},
            "viewCountText": {"simpleText": views},
        }
    }


def channel_renderer(cid, title):
    return {"channelRenderer": {"channelId": cid, "title": {"simpleText": title}, "descriptionSnippet": {"runs": []}}}


def test_lockups_skip_playlists_and_read_fields():
    data = {"items": [lockup("V1", "Title", "Chan", "UC1"), {"lockupViewModel": {"contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST"}}]}
    assert itb.lockups(data) == [{"video_id": "V1", "title": "Title", "channel": "Chan", "channel_id": "UC1", "views": 16000}]


def test_watch_info_and_search_parsers():
    data = {
        "contents": [
            {"videoPrimaryInfoRenderer": {"title": {"runs": [{"text": "Src title"}]}}},
            {"videoOwnerRenderer": {"title": {"runs": [{"text": "Src", "navigationEndpoint": {"browseEndpoint": {"browseId": "UCS"}}}]}}},
            {"attributedDescription": {"content": "desc"}},
            lockup("V1", "Rel", "C", "UC1"),
        ]
    }
    w = itb.watch_info(data)
    assert (w["title"], w["channel"], w["channel_id"], w["description"]) == ("Src title", "Src", "UCS", "desc")
    assert [v["video_id"] for v in w["related"]] == ["V1"]

    s = {"contents": [video_renderer("V2", "T", "C2", "UC2"), channel_renderer("UC3", "Kênh 3")]}
    assert itb.search_videos(s)[0] == {"video_id": "V2", "title": "T", "channel": "C2", "channel_id": "UC2", "views": 1000}
    assert itb.search_channels(s) == [{"channel_id": "UC3", "channel": "Kênh 3", "description": ""}]


# ---------------------------------------------------------------------------
# Chấm điểm
# ---------------------------------------------------------------------------


def test_corpus_similarity_handles_word_variants():
    c = Corpus({"a": "what if you were stronger", "b": "what if you were 100x strong", "c": "minecraft survival day"})
    assert c.similarity("a", "b") > 0.3
    assert c.similarity("a", "c") == 0


def test_rank_channels_filters_off_topic_and_matches_any_language_version():
    cands = [
        {"channel_id": "pt", "text": "e se você fosse forte | e se você pudesse voar", "signals": {"related": 1}},
        {"channel_id": "en", "text": "what if you could fly | what if you were strong", "signals": {"search": 3}},
        {"channel_id": "game", "text": "minecraft hardcore survival", "signals": {"related": 8}},
    ]
    # Nguồn có bản gốc tiếng Bồ Đào Nha và bản YouTube dịch sang tiếng Anh.
    out = rank_channels(["e se você fosse 10 vezes mais forte", "what if you were 10 times stronger"], cands)
    assert {r["channel_id"] for r in out} == {"pt", "en"}  # kênh game nhiều tín hiệu nhưng lạc đề
    assert all("text" not in r for r in out)
    assert out[0]["score"] >= out[1]["score"]


def test_rank_videos_rewards_signals():
    cands = [
        {"video_id": "a", "text": "what if you were stronger", "signals": {"related": 4}},
        {"video_id": "b", "text": "what if you were stronger", "signals": {"search": 0.2}},
    ]
    out = rank_videos(["what if you were stronger"], cands)
    assert [r["video_id"] for r in out] == ["a", "b"]
    assert out[0]["score"] == 100.0


# ---------------------------------------------------------------------------
# Cả luồng với YouTube giả (innertube + Data API + autocomplete)
# ---------------------------------------------------------------------------

SRC, C1, C2, C3, TOPIC = "UCsrc", "UCwhatif1", "UCgamer", "UCwhatif3", "UCtopic"

CHANNELS = {
    SRC: ("What If Lab", "BR", '"what if" superpowers', ["What if you could fly", "What if you were invisible"]),
    C1: ("Ifology", "US", "what if animation", ["What if you were 100x faster", "What if you could fly forever"]),
    C2: ("Block Gamer", "US", "minecraft gaming", ["Minecraft hardcore day 100", "Minecraft speedrun world record"]),
    C3: ("Whatology", "US", "what if hypothetical", ["What if you were invisible for a day", "What if you could fly"]),
}
# Mỗi kênh 6 video dài 1.000 view → trung vị 1.000 để tính outlier.
UPLOADS = {cid: [f"{cid}_{i}" for i in range(6)] for cid in CHANNELS}


def _video(vid, cid, title, views, duration="PT10M", tags=None):
    return {
        "id": vid,
        "snippet": {"channelId": cid, "title": title, "description": "", "tags": tags or [], "liveBroadcastContent": "none",
                    "publishedAt": "2026-10-01T00:00:00Z"},
        "statistics": {"viewCount": str(views)},
        "contentDetails": {"duration": duration},
    }


VIDEOS = {
    "SRCVIDEO001": _video("SRCVIDEO001", SRC, "What if you were 10 times stronger", 17000, tags=["what if", "superpowers"]),
    "V1aaaaaaaaa": _video("V1aaaaaaaaa", C1, "What if you were 100 times stronger", 5000),
    "V2aaaaaaaaa": _video("V2aaaaaaaaa", C2, "Minecraft hardcore survival day 100", 90000),
    "V5aaaaaaaaa": _video("V5aaaaaaaaa", C3, "What if you were stronger than everyone", 800),
}
for cid, vids in UPLOADS.items():
    for i, vid in enumerate(vids):
        VIDEOS[vid] = _video(vid, cid, CHANNELS[cid][3][i % 2], 1000)


def _channel_item(cid):
    title, country, keywords, _ = CHANNELS[cid]
    return {
        "id": cid,
        "snippet": {"title": title, "country": country, "customUrl": f"@{title.lower().replace(' ', '')}",
                    "description": f"{title} videos", "publishedAt": "2024-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "5000", "videoCount": "6", "hiddenSubscriberCount": False},
        "contentDetails": {"relatedPlaylists": {"uploads": f"UU{cid}"}},
        "brandingSettings": {"channel": {"keywords": keywords}},
    }


def data_api(counter):
    def handler(request: httpx.Request) -> httpx.Response:
        ep = request.url.path.rsplit("/", 1)[-1]
        counter[ep] = counter.get(ep, 0) + 1
        p = request.url.params
        if ep == "videos":
            return httpx.Response(200, json={"items": [VIDEOS[i] for i in p["id"].split(",") if i in VIDEOS]})
        if ep == "channels":
            if "forHandle" in p:
                return httpx.Response(200, json={"items": [{"id": SRC}] if p["forHandle"] == "@whatiflab" else []})
            return httpx.Response(200, json={"items": [_channel_item(c) for c in p["id"].split(",") if c in CHANNELS]})
        if ep == "playlistItems":
            cid = p["playlistId"][2:]
            return httpx.Response(200, json={"items": [
                {"contentDetails": {"videoId": v}, "snippet": {"title": VIDEOS[v]["snippet"]["title"], "resourceId": {"videoId": v}}}
                for v in UPLOADS.get(cid, [])
            ]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def youtube_web(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "suggestqueries.google.com":
            return httpx.Response(200, text=json.dumps(["q", ["what if you were stronger"]]))
        ep = request.url.path.split("/youtubei/v1/", 1)[-1]
        body = json.loads(request.content)
        calls.append((ep, body["context"]["client"]["gl"]))
        if ep == "next":
            return httpx.Response(200, json={"contents": [
                {"videoPrimaryInfoRenderer": {"title": {"runs": [{"text": "What if you were 10 times stronger"}]}}},
                lockup("V1aaaaaaaaa", "What if you were 100 times stronger", "Ifology", C1),
                lockup("V2aaaaaaaaa", "Minecraft hardcore survival day 100", "Block Gamer", C2),
                lockup("V3aaaaaaaaa", "Stronger (song)", "Singer - Topic", TOPIC),
                lockup(UPLOADS[SRC][0], "What if you could fly", "What If Lab", SRC),
            ]})
        if ep == "browse":
            return httpx.Response(200, json={"contents": [lockup(v, VIDEOS[v]["snippet"]["title"], "What If Lab", SRC) for v in UPLOADS[SRC]]})
        if ep == "search":
            if body["params"] == itb.SEARCH_CHANNELS:
                return httpx.Response(200, json={"contents": [channel_renderer(C3, "Whatology"), channel_renderer(C1, "Ifology")]})
            return httpx.Response(200, json={"contents": [
                video_renderer("V1aaaaaaaaa", "What if you were 100 times stronger", "Ifology", C1),
                video_renderer("V5aaaaaaaaa", "What if you were stronger than everyone", "Whatology", C3),
            ]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _find(conn, url, **kw):
    hits, calls = {}, []
    rid = similar.find_similar(
        conn, url, "US", api_key="k", yt_transport=data_api(hits), it_transport=youtube_web(calls), **kw
    )
    return similar.get_run(conn, rid), hits, calls


def test_find_similar_from_video_url(conn):
    run, hits, calls = _find(conn, "https://www.youtube.com/watch?v=SRCVIDEO001")
    res = run["result"]

    assert run["kind"] == "video" and run["label"] == "What if you were 10 times stronger"
    assert res["locales"] == ["US", "BR"]  # thêm nước của kênh gốc
    assert {gl for ep, gl in calls if ep == "next"} == {"US", "BR"}

    channels = {c["channel_id"]: c for c in res["channels"]}
    assert set(channels) == {C1, C3}  # bỏ kênh gốc, kênh game lạc đề, kênh nhạc "- Topic"
    assert channels[C1]["signals"]["related"] == 2  # Up next ở 2 thị trường
    assert channels[C1]["subs"] == 5000

    videos = {v["video_id"]: v for v in res["videos"]}
    assert set(videos) == {"V1aaaaaaaaa", "V5aaaaaaaaa"}
    assert videos["V1aaaaaaaaa"]["outlier"] == 5.0 and videos["V1aaaaaaaaa"]["level"] == "Vượt trội"
    assert videos["V1aaaaaaaaa"]["fmt"] == "long"
    assert res["videos"][0]["video_id"] == "V1aaaaaaaaa"  # vừa ở Up next vừa ở search

    assert similar.list_runs(conn)[0]["n_channels"] == 2


def test_find_similar_from_handle_uses_top_videos_and_cache(conn):
    run, hits, calls = _find(conn, "https://www.youtube.com/@whatiflab", with_source_market=False)
    assert run["kind"] == "channel"
    assert run["result"]["locales"] == ["US"]
    assert len(run["result"]["seeds"]) == 4  # 4 video nhiều view nhất trong 15 video gần nhất
    assert run["result"]["channels"]

    _, _, calls2 = _find(conn, "https://www.youtube.com/@whatiflab", with_source_market=False)
    assert calls2 == []  # lần 2: innertube lấy hết từ cache


def test_find_similar_unknown_handle(conn):
    with pytest.raises(similar.SimilarError):
        _find(conn, "https://www.youtube.com/@khongco")


def test_delete_run(conn):
    run, _, _ = _find(conn, "https://youtu.be/SRCVIDEO001")
    similar.delete_run(conn, run["id"])
    assert similar.get_run(conn, run["id"]) is None
