"""AI (Command Code, API chuẩn OpenAI): cấu hình, giới hạn lượt, phân loại từ khoá cho Radar, chạy prompt."""

import json
import re
from datetime import timedelta

import pytest

from yt_niche.core import radar as rd
from yt_niche.services import ai, radar
from tests.test_radar import NOW, fake, us_only  # noqa: F401 (fixture)

TYPES = {"katseye": "person", "dog training tips": "how_to", "bão narra": "news"}


@pytest.fixture
def fake_ai(conn, monkeypatch):
    calls = []

    def post(url, payload):
        calls.append(payload)
        user = payload["messages"][-1]["content"]
        if user.startswith("Keywords:"):
            kws = re.findall(r"^(\d+)\. (.+)$", user, re.M)
            items = [{"i": int(i), "type": TYPES.get(kw, "evergreen"), "vi": f"nghĩa {kw}", "why": "lý do"} for i, kw in kws]
            content = "```json\n" + json.dumps({"items": items}, ensure_ascii=False) + "\n```"
        else:
            content = "Trả lời mẫu"
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setenv("COMMANDCODE_API_KEY", "sk-test-1234567890")
    monkeypatch.setattr(ai, "_post", post)
    ai.save_config(conn, {"enabled": True, "model_fast": "fast-m", "model_deep": "deep-m"})
    return calls


def test_disabled_by_default(conn, monkeypatch):
    monkeypatch.setenv("COMMANDCODE_API_KEY", "sk-test-1234567890")
    assert not ai.enabled(conn)
    with pytest.raises(ai.AIError):
        ai.chat(conn, "fast", [{"role": "user", "content": "hi"}])
    assert ai.masked_key() == "sk-t…7890"


def test_feature_toggle_and_models(conn, fake_ai):
    assert ai.enabled(conn, "radar") and ai.enabled(conn, "prompts")
    ai.save_config(conn, {"features": {"radar": False, "prompts": True}})
    assert not ai.enabled(conn, "radar") and ai.enabled(conn, "prompts")
    ai.run_prompt(conn, "xin chào")
    assert fake_ai[-1]["model"] == "deep-m"


def test_daily_limit_and_usage(conn, fake_ai):
    ai.save_config(conn, {"max_requests_per_day": 2})
    ai.run_prompt(conn, "a")
    ai.run_prompt(conn, "b")
    with pytest.raises(ai.AIError, match="hết 2 lượt"):
        ai.run_prompt(conn, "c")
    u = ai.usage_today(conn)
    assert u == {"requests": 2, "prompt_tokens": 20, "completion_tokens": 10}


def test_run_prompt_cached(conn, fake_ai):
    assert ai.cached_answer(conn, "p") is None
    assert ai.run_prompt(conn, "p") == "Trả lời mẫu"
    assert ai.cached_answer(conn, "p") == "Trả lời mẫu"


def test_parse_json():
    assert ai.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai.parse_json('Đây: {"a": [1]} xong') == {"a": [1]}
    with pytest.raises(ai.AIError):
        ai.parse_json("không có json")


def test_classify_cached_and_batched(conn, fake_ai, monkeypatch):
    monkeypatch.setattr(ai, "RADAR_BATCH", 2)
    got = ai.classify_keywords(conn, ["katseye", "dog training tips", "bão narra"])
    assert len(fake_ai) == 2  # 3 từ, lô 2
    assert got["katseye"]["short"] and not got["dog training tips"]["short"]
    assert got["bão narra"]["label"] == "Tin tức / sự kiện"
    ai.classify_keywords(conn, ["KATSEYE"])  # cùng từ (khác hoa thường): dùng cache
    assert len(fake_ai) == 2


def test_radar_drops_short_term_before_trends(conn, us_only, fake_ai):
    radar.tick(conn, now=NOW)  # quét danh mục: thêm từ khoá chờ, AI phân loại ngay
    out = {k["keyword"]: k for k in radar.keywords(conn, [rd.EXCLUDED])}
    assert "katseye" in out and out["katseye"]["reason"].startswith("AI: Người nổi tiếng")
    assert radar.keywords(conn, [rd.PENDING])[0]["ai_vi"] == "nghĩa dog training tips"
    radar.tick(conn, now=NOW + timedelta(minutes=10))  # Trends kiểm từ còn lại
    assert ("entity", "katseye") not in us_only  # không tốn lượt Google cho từ AI đã loại
    kept = {k["keyword"]: k for k in radar.keywords(conn, [rd.PASSED, rd.PENDING])}
    assert kept["dog training tips"]["ai_vi"] == "nghĩa dog training tips"


def test_fill_viability_keeps_written_promise(conn, fake_ai, monkeypatch):
    ideas = [f"⭐ Ý tưởng {i}" for i in range(10)] + [f"{i}. Ý tưởng {i}" for i in range(10, 35)] + ["Ý tưởng 0"]
    answer = json.dumps({"promise": "Kênh giúp người mới huấn luyện chó", "ideas": ideas}, ensure_ascii=False)
    monkeypatch.setattr(ai, "_post", lambda url, payload: fake_ai.append(payload) or {"choices": [{"message": {"content": answer}}]})
    got = ai.fill_viability(conn, "prompt ngách", "vi")
    assert got["promise"] == "Kênh giúp người mới huấn luyện chó" and len(got["ideas"]) == 30  # cắt còn 30
    assert got["ideas"][0] == "Ý tưởng 0" and got["ideas"][10] == "Ý tưởng 10"  # bỏ ⭐ và số thứ tự
    assert "30 ý tưởng" in fake_ai[-1]["messages"][0]["content"] and fake_ai[-1]["model"] == "deep-m"
    assert ai.fill_viability(conn, "prompt ngách", "vi", "Lời hứa của tôi")["promise"] == "Lời hứa của tôi"


def test_radar_ai_error_does_not_block(conn, us_only, fake_ai, monkeypatch):
    def boom(url, payload):
        raise ai.AIError("HTTP 500: lỗi")

    monkeypatch.setattr(ai, "_post", boom)
    radar.tick(conn, now=NOW)
    r = radar.tick(conn, now=NOW + timedelta(minutes=10))
    assert r["action"] == "check" and "AI lỗi" in r["detail"]


def test_effort_sent_and_dropped_when_unsupported(conn, fake_ai, monkeypatch):
    ai.save_config(conn, {"effort_deep": "high"})
    ai.run_prompt(conn, "x")
    assert fake_ai[-1]["reasoning_effort"] == "high"

    sent = []

    def picky(url, payload):
        sent.append(dict(payload))
        if "reasoning_effort" in payload:
            raise ai.AIError("HTTP 400: Unsupported parameter: 'reasoning_effort'")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai, "_post", picky)
    ai.save_config(conn, {"model_deep": "no-reasoning-model"})
    assert ai.run_prompt(conn, "y") == "ok"
    assert len(sent) == 2 and "reasoning_effort" not in sent[1]
    ai.run_prompt(conn, "z")  # đã nhớ: không gửi effort nữa, không tốn lượt lỗi
    assert len(sent) == 3 and "reasoning_effort" not in sent[2]


def test_empty_answer_is_error_not_cached(conn, fake_ai, monkeypatch):
    ai.save_config(conn, {"effort_deep": "high"})
    sent = []

    def thinking_only(url, payload):
        sent.append(payload)
        return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}

    monkeypatch.setattr(ai, "_post", thinking_only)
    with pytest.raises(ai.AIError, match="Hạ Effort"):
        ai.run_prompt(conn, "dịch")
    assert ai.cached_answer(conn, "dịch") is None  # câu rỗng không được lưu
    assert sent[0]["max_tokens"] == 8000 + ai.REASONING_BUDGET["high"]  # chừa chỗ cho phần suy nghĩ


def test_effort_values_and_nearest_fallback(conn, fake_ai, monkeypatch):
    ai.save_config(conn, {"effort_fast": "minimal"})  # giá trị cũ không còn trong danh sách
    assert ai.get_config(conn)["effort_fast"] == "off"
    err = 'HTTP 400: Invalid option: expected one of "off"|"low"|"medium"|"high"|"xhigh"|"max"'
    assert ai._nearest_effort(err, "minimal") == "off"
    assert ai._nearest_effort(err, "medium") is None  # đã hợp lệ: không phải lỗi effort

    sent = []

    def strict(url, payload):
        sent.append(dict(payload))
        if payload.get("reasoning_effort") not in ("low", "high"):
            raise ai.AIError('HTTP 400: Invalid option: expected one of "low"|"high"')
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai, "_post", strict)
    ai.save_config(conn, {"model_deep": "strict-model", "effort_deep": "medium"})
    assert ai.run_prompt(conn, "q") == "ok"
    assert [p["reasoning_effort"] for p in sent] == ["medium", "low"]
    ai.run_prompt(conn, "q2")  # đã nhớ mức thay thế
    assert sent[-1]["reasoning_effort"] == "low" and len(sent) == 3


def test_model_that_thinks_despite_off_gets_room(conn, fake_ai, monkeypatch):
    ai.save_config(conn, {"effort_fast": "off"})
    sent = []

    def thinker(url, payload):
        sent.append(payload["max_tokens"])
        if payload["max_tokens"] < 1000:  # suy nghĩ ~450 token dù effort "off": hết chỗ trả lời
            return {"choices": [{"message": {"content": "", "reasoning_content": "We need..."}, "finish_reason": "length"}]}
        return {"choices": [{"message": {"content": '{"items": [{"i": 0, "type": "news", "vi": "tin", "why": "sự kiện"}]}'}}]}

    monkeypatch.setattr(ai, "_post", thinker)
    got = ai.classify_keywords(conn, ["bão narra"])
    assert got["bão narra"]["short"] and sent == [320, 320 + ai.THINK_FLOOR]  # tự hỏi lại với thêm chỗ
    ai.classify_keywords(conn, ["katseye"])  # đã nhớ: lần sau chừa chỗ ngay từ đầu, chỉ 1 lượt
    assert sent[2:] == [320 + ai.THINK_FLOOR]
