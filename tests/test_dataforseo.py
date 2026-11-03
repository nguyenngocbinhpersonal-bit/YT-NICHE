"""DataForSEO: nguồn Google Trends dự phòng khi Google chặn (429). Chỉ dùng khi đã bật, có giới hạn lượt/ngày."""

import asyncio
from datetime import date, timedelta

import pytest

from yt_niche.core import radar as rd
from yt_niche.services import radar
from yt_niche.sources import dataforseo as dfs
from yt_niche.sources import trends
from tests.test_radar import NOW, STEADY_UP, fake, us_only  # noqa: F401 (fixture)
from tests.test_trends_throttle import fake as fake_google  # noqa: F401 (fixture)

WEEKLY = [[(date(2021, 10, 17) + timedelta(weeks=i)).isoformat(), 20 + i // 10] for i in range(261)]

LOCATIONS = [
    {"location_code": 2840, "location_name": "United States", "country_iso_code": "US", "location_type": "Country"},
    {"location_code": 21137, "location_name": "California", "country_iso_code": "US", "location_type": "State"},
    {"location_code": 2704, "location_name": "Vietnam", "country_iso_code": "VN", "location_type": "Country"},
]


def _ok(result, cost=0.0):
    return {"status_code": 20000, "cost": cost, "tasks": [{"status_code": 20000, "result": result}]}


@pytest.fixture
def backup(conn, monkeypatch):
    sent = []

    def request(method, path, payload=None):
        sent.append((path, payload))
        if path.endswith("/locations"):
            return _ok(LOCATIONS)
        task = payload[0]
        if task["item_types"] == ["google_trends_queries_list"]:
            data = {"top": [{"query": "dog training tips", "value": 100}], "rising": [{"query": "puppy class", "value": 450}]}
            return _ok([{"items": [{"type": "google_trends_queries_list", "data": data}]}], 0.009)
        series = STEADY_UP if task["time_range"] == "past_30_days" else WEEKLY
        points = [{"date_from": d, "date_to": d, "values": [v] * len(task["keywords"])} for d, v in series]
        return _ok([{"items": [{"type": "google_trends_graph", "data": points}]}], 0.009)

    monkeypatch.setenv("DATAFORSEO_LOGIN", "someone-login")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "secret-pass")
    monkeypatch.setattr(dfs, "_request", request)
    dfs.save_config(conn, {"enabled": True})
    return sent


def test_off_by_default(conn, monkeypatch):
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    assert dfs.unavailable_reason(conn) == "chưa bật DataForSEO"
    dfs.save_config(conn, {"enabled": True})
    assert "login" in dfs.unavailable_reason(conn)


def test_masked_login_and_keyword_cleaning(backup):
    assert dfs.masked_login() == "som…ogin"
    assert dfs.clean_keyword('t-shirt "design" (2026)') == "t shirt design 2026"


def test_dig_uses_backup_when_google_throttled(conn, fake_google, backup):
    fake_google.blocked = {"today 5-y"}
    r = asyncio.run(trends.get_trends(conn, "dog training", "US"))
    assert r["provider"] == "dataforseo" and r["errors"] == {} and len(r["series"]["5y"]) == 261
    assert r["derived"] == ["12m", "3m"]  # suy ra, không tốn thêm lượt trả phí
    graph = [p for path, p in backup if path.endswith("/live")]
    assert len(graph) == 1 and graph[0][0]["location_code"] == 2840 and graph[0][0]["type"] == "youtube"
    assert graph[0][0]["time_range"] == "past_5_years"
    assert dfs.usage_today(conn) == {"tasks": 1, "cost": 0.009}

    fake_google.calls.clear()  # đang nghỉ: đi thẳng DataForSEO, không gọi Google
    asyncio.run(trends.get_trends(conn, "cat training", "US"))
    assert fake_google.calls == [] and dfs.usage_today(conn)["tasks"] == 2


def test_daily_limit_and_backup_error(conn, fake_google, backup, monkeypatch):
    dfs.save_config(conn, {"max_tasks_per_day": 1})
    fake_google.blocked = {"today 5-y"}
    asyncio.run(trends.get_trends(conn, "a", "US"))
    r = asyncio.run(trends.get_trends(conn, "b", "US"))  # hết lượt: như khi chưa có dự phòng
    assert "tạm nghỉ gọi Trends" in r["errors"]["5y"]

    dfs.save_config(conn, {"max_tasks_per_day": 10})

    def broken(method, path, payload=None):
        raise dfs.BackupError("40200: Payment Required.")

    monkeypatch.setattr(dfs, "_request", broken)
    r = asyncio.run(trends.get_trends(conn, "c", "US"))
    assert "Payment Required" in trends.describe_errors(r["errors"])


def test_request_errors(monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", "x")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "y")

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    monkeypatch.setattr(dfs.httpx, "request", lambda *a, **k: Resp({"status_code": 40100, "status_message": "Not authorized"}))
    with pytest.raises(dfs.BackupError, match="Not authorized"):
        dfs._request("GET", "/appendix/user_data")
    bad_task = {"status_code": 20000, "tasks": [{"status_code": 40501, "status_message": "Invalid Field"}]}
    monkeypatch.setattr(dfs.httpx, "request", lambda *a, **k: Resp(bad_task))
    with pytest.raises(dfs.BackupError, match="Invalid Field"):
        dfs._request("POST", "/x", [{}])


def test_radar_uses_backup_during_cooldown(conn, us_only, backup):
    trends.start_cooldown(conn)
    r = radar.tick(conn, now=NOW)  # Google đang chặn: quét danh mục qua DataForSEO
    assert r["action"] == "discover" and "[DataForSEO]" in r["detail"] and r.get("paid") == 1
    assert ("discover", "US", 66) not in us_only
    assert {k["keyword"] for k in radar.keywords(conn, [rd.PENDING])} == {"dog training tips", "puppy class"}

    r = radar.tick(conn, now=NOW + timedelta(minutes=10))
    assert r["action"] == "check" and "[DataForSEO]" in r["detail"] and r["requests"] == 0 and r["paid"] == 2
    assert dfs.usage_today(conn)["tasks"] == 3  # kiểm tra: 30 ngày YouTube Search + Google Search = 2 lượt trả phí
    assert {p[0]["type"] for path, p in backup if p and p[0].get("time_range") == "past_30_days"} == {"youtube", "web"}

    dfs.save_config(conn, {"radar": False})
    r = radar.tick(conn, now=NOW + timedelta(minutes=20))
    assert r["action"] == "cooldown" and dfs.usage_today(conn)["tasks"] == 3


def test_radar_switches_to_backup_on_429(conn, us_only, backup, monkeypatch):
    def blocked(geo, cat):
        raise trends.Throttled(trends.THROTTLED)

    monkeypatch.setattr(trends, "category_queries", blocked)
    r = radar.tick(conn, now=NOW)
    assert r["throttled"] and r["action"] == "discover" and "→" in r["detail"] and trends.cooldown_until(conn)


def test_entity_lookup_not_counted_as_trends_request(conn, us_only, monkeypatch):
    radar.tick(conn, now=NOW)
    # katseye bị loại nhờ gợi ý thực thể (không tính vào nhịp), rồi kiểm tiếp từ sau trong cùng lượt.
    r = radar.tick(conn, now=NOW + timedelta(minutes=10))
    assert ("entity", "katseye") in us_only and ("series", "dog training tips", "web") in us_only
    assert r["requests"] == 4  # 30 ngày YouTube Search + Google Search
