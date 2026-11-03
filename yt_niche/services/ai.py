"""AI qua API chuẩn OpenAI (mặc định Command Code). Cấu hình ở Cài đặt → AI; tắt hoặc lỗi thì app chạy như không có AI.

Chỉ gửi dữ liệu công khai (từ khoá, tiêu đề, số liệu kênh, nội dung prompt). Không gửi email, API key YouTube hay đường dẫn máy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime

import httpx

from yt_niche import config as cfg
from yt_niche.db import cache

CONFIG_KEY = "ai_config"
TIERS = {"fast": "Nhanh (phân loại, dịch)", "deep": "Sâu (phân tích, ý tưởng)"}
FEATURES = {
    "radar": "Radar: phân loại từ khoá ngắn hạn / lâu dài và dịch nghĩa tiếng Việt (trước khi tốn lượt Google Trends)",
    "prompts": "Nút \"🤖 Chạy bằng AI\" cạnh mọi prompt có sẵn (đọc kết quả đào, ngách con, 30 ý tưởng, so sánh, dịch từ khoá…)",
}
# reasoning_effort theo Command Code; "" = không gửi (để model tự chọn). Nhà cung cấp khác chê giá trị nào thì
# app tự đổi sang mức gần nhất họ nhận (đọc từ lỗi "expected one of …").
EFFORTS = {"": "Mặc định (không gửi)", "off": "Tắt suy nghĩ", "low": "Thấp", "medium": "Vừa", "high": "Cao",
           "xhigh": "Rất cao", "max": "Tối đa"}
EFFORT_RANK = {"off": 0, "none": 0, "minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
LEGACY_EFFORT = {"minimal": "off", "none": "off"}
DEFAULT_CONFIG = {
    "enabled": False,
    "base_url": cfg.AI_BASE_URL,
    "model_fast": "",
    "model_deep": "",
    "effort_fast": "low",
    "effort_deep": "medium",
    "models": [],  # danh sách lấy từ /models lúc bấm "Kiểm tra kết nối"
    "features": {k: True for k in FEATURES},
    "max_requests_per_day": cfg.AI_MAX_REQUESTS_PER_DAY,
}
TIMEOUT = 120
CLASSIFY_TTL = 90 * 86400
PROMPT_TTL = 30 * 86400
RADAR_BATCH = 40

# Loại từ khoá do AI trả về → nhãn tiếng Việt; loại có short=True bị Radar bỏ trước khi kiểm Trends.
KEYWORD_TYPES = {
    "evergreen": ("Lâu dài", False),
    "seasonal": ("Theo mùa", False),
    "how_to": ("Hướng dẫn / kỹ năng", False),
    "product": ("Sản phẩm / dịch vụ", False),
    "news": ("Tin tức / sự kiện", True),
    "person": ("Người nổi tiếng", True),
    "sports_match": ("Trận / giải đấu", True),
    "entertainment_release": ("Phim / nhạc / show mới", True),
    "brand_launch": ("Ra mắt sản phẩm / game mới", True),
    "other_short": ("Ngắn hạn khác", True),
}


# Tham số tuỳ chọn → chữ nhận ra trong lỗi khi model không hỗ trợ nó.
# Token thêm cho phần suy nghĩ theo mức effort (cộng vào max_tokens của từng việc).
REASONING_BUDGET = {"minimal": 1000, "low": 3000, "medium": 8000, "high": 20000, "xhigh": 32000, "max": 48000}
OPTIONAL_PARAMS = {"reasoning_effort": ("reasoning",), "response_format": ("response_format", "json"), "temperature": ("temperature",)}
# Có model vẫn suy nghĩ dù effort "off" (vd. deepseek-v4.1-flash trên Command Code, đo 10/2026: ~450 token suy nghĩ cho
# 2 từ khoá). Gặp lần đầu (trả lời rỗng vì hết token) thì nhớ model đó, từ đó luôn chừa ít nhất THINK_FLOOR token.
THINK_FLOOR = 4000
THINKS_TTL = 180 * 86400
_UNSUPPORTED: set[tuple[str, str]] = set()
_EFFORT_FIX: dict[tuple[str, str], str] = {}


def _nearest_effort(error: str, effort: str) -> str | None:
    """Lỗi kiểu 'expected one of "off"|"low"|…': chọn giá trị được nhận gần mức đang gửi nhất."""
    if "expected one of" not in error.lower():
        return None
    allowed = [v for v in re.findall(r'"([a-z]+)"', error) if v in EFFORT_RANK]
    if not allowed or effort in allowed:
        return None
    rank = EFFORT_RANK.get(effort, EFFORT_RANK["medium"])
    return min(allowed, key=lambda v: (abs(EFFORT_RANK[v] - rank), EFFORT_RANK[v]))


class AIError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cấu hình
# ---------------------------------------------------------------------------


def api_key() -> str:
    return (os.getenv("COMMANDCODE_API_KEY") or os.getenv("AI_API_KEY") or "").strip()


def save_api_key(key: str) -> None:
    from dotenv import set_key

    cfg.ENV_PATH.touch(exist_ok=True)
    set_key(str(cfg.ENV_PATH), "COMMANDCODE_API_KEY", key.strip(), quote_mode="never")
    os.environ["COMMANDCODE_API_KEY"] = key.strip()


def masked_key() -> str:
    k = api_key()
    return f"{k[:4]}…{k[-4:]}" if len(k) > 8 else ("****" if k else "")


def get_config(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (CONFIG_KEY,)).fetchone()
    saved = json.loads(row["value"]) if row else {}
    out = {**DEFAULT_CONFIG, **saved}
    out["features"] = {**DEFAULT_CONFIG["features"], **saved.get("features", {})}
    for tier in TIERS:
        e = out[f"effort_{tier}"]
        out[f"effort_{tier}"] = e if e in EFFORTS else LEGACY_EFFORT.get(e, "")
    return out


def save_config(conn: sqlite3.Connection, config: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
        (CONFIG_KEY, json.dumps({**get_config(conn), **config}, ensure_ascii=False)),
    )


def enabled(conn: sqlite3.Connection, feature: str | None = None) -> bool:
    c = get_config(conn)
    if not (c["enabled"] and api_key() and c["model_fast"] and c["model_deep"]):
        return False
    return feature is None or bool(c["features"].get(feature))


# ---------------------------------------------------------------------------
# Lượt dùng trong ngày
# ---------------------------------------------------------------------------


def _usage_key(now: datetime | None = None) -> str:
    return f"ai_usage:{(now or datetime.now()).astimezone().date().isoformat()}"


def usage_today(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (_usage_key(),)).fetchone()
    return json.loads(row["value"]) if row else {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _add_usage(conn: sqlite3.Connection, usage: dict) -> None:
    u = usage_today(conn)
    u["requests"] += 1
    u["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    u["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (_usage_key(), json.dumps(u)))


# ---------------------------------------------------------------------------
# Gọi API
# ---------------------------------------------------------------------------


def _headers() -> dict:
    return {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error") or body.get("message")
    except ValueError:
        msg = r.text
    return f"HTTP {r.status_code}: {str(msg or '')[:200]}"


def _post(url: str, payload: dict) -> dict:
    """Tách riêng để test thay thế."""
    try:
        r = httpx.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise AIError(f"Không kết nối được AI: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    return r.json()


def list_models(base_url: str | None = None) -> list[str]:
    """Gọi /models (kiểm tra key + lấy tên model). Không tính vào lượt dùng."""
    if not api_key():
        raise AIError("Chưa có API key.")
    url = (base_url or cfg.AI_BASE_URL).rstrip("/") + "/models"
    try:
        r = httpx.get(url, headers=_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise AIError(f"Không kết nối được {url}: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    body = r.json()
    items = body.get("data", body) if isinstance(body, dict) else body
    return sorted({m["id"] if isinstance(m, dict) else str(m) for m in items})


def _thinks_key(model: str) -> str:
    return f"ai_thinks:{model}"


def _send(url: str, payload: dict, model: str, effort: str | None) -> dict:
    """Gửi; tham số tuỳ chọn bị chê thì sửa / bỏ, nhớ lại rồi gửi lại."""
    while True:
        try:
            return _post(url, payload)
        except AIError as e:
            if "reasoning_effort" in payload and (alt := _nearest_effort(str(e), payload["reasoning_effort"])):
                _EFFORT_FIX[(model, effort)] = alt  # nhà cung cấp không nhận mức này: dùng mức gần nhất họ nhận
                payload["reasoning_effort"] = alt
                continue
            # Model không hỗ trợ tham số tuỳ chọn (effort, JSON mode, temperature): bỏ nó, nhớ lại, thử lại.
            bad = next((k for k, words in OPTIONAL_PARAMS.items() if k in payload and any(w in str(e).lower() for w in words)), None)
            if bad is None:
                raise
            payload.pop(bad)
            _UNSUPPORTED.add((bad, model))


def chat(conn: sqlite3.Connection, tier: str, messages: list[dict], max_tokens: int = 4000, json_mode: bool = False) -> str:
    c = get_config(conn)
    if not enabled(conn):
        raise AIError("AI chưa bật hoặc chưa đủ cấu hình (Cài đặt → AI).")
    if usage_today(conn)["requests"] >= c["max_requests_per_day"]:
        raise AIError(f"Đã hết {c['max_requests_per_day']} lượt AI hôm nay (đổi ở Cài đặt → AI).")
    model = c[f"model_{tier}"]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    budget = 0
    if (effort := c.get(f"effort_{tier}")) and ("reasoning_effort", model) not in _UNSUPPORTED:
        effort = _EFFORT_FIX.get((model, effort), effort)
        payload["reasoning_effort"] = effort
        budget = REASONING_BUDGET.get(effort, 0)
    if cache.get(conn, _thinks_key(model)):
        budget = max(budget, THINK_FLOOR)
    # Model suy nghĩ tính cả phần suy nghĩ vào max_tokens: thêm chỗ để còn token cho câu trả lời.
    payload["max_tokens"] = max_tokens + budget
    if json_mode and ("response_format", model) not in _UNSUPPORTED:
        payload["response_format"] = {"type": "json_object"}
    url = c["base_url"].rstrip("/") + "/chat/completions"
    for attempt in range(2):
        body = _send(url, payload, model, effort)
        _add_usage(conn, body.get("usage") or {})
        try:
            choice = body["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise AIError(f"Phản hồi AI không đúng định dạng: {str(body)[:200]}") from e
        if content.strip():
            return content
        if choice.get("finish_reason") != "length":
            raise AIError(f"Model {model} trả về câu trả lời rỗng. Thử lại, hạ Effort hoặc chọn model khác.")
        if attempt == 0 and not cache.get(conn, _thinks_key(model)):
            # Hết token khi đang suy nghĩ (kể cả khi đã tắt suy nghĩ): nhớ model này, chừa thêm chỗ rồi hỏi lại một lần.
            cache.put(conn, _thinks_key(model), True, THINKS_TTL)
            payload["max_tokens"] = max_tokens + max(budget, THINK_FLOOR)
            continue
        break
    raise AIError(
        f"Model {model} dùng hết {payload['max_tokens']:,} token cho phần suy nghĩ mà chưa kịp trả lời. "
        "Hạ Effort (Cài đặt → AI) hoặc chọn model khác."
    )


def parse_json(text: str):
    """Lấy JSON từ câu trả lời (bỏ ```json … ``` và chữ thừa quanh nó)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise AIError(f"AI không trả về JSON hợp lệ: {text[:150]}")


# ---------------------------------------------------------------------------
# Chạy prompt có sẵn
# ---------------------------------------------------------------------------


def _prompt_key(prompt: str) -> str:
    return "ai_prompt:" + hashlib.sha1(prompt.encode()).hexdigest()


def cached_answer(conn: sqlite3.Connection, prompt: str) -> str | None:
    return cache.get(conn, _prompt_key(prompt))


def run_prompt(conn: sqlite3.Connection, prompt: str, tier: str = "deep") -> str:
    system = "Bạn là chuyên gia nghiên cứu ngách YouTube. Trả lời bằng tiếng Việt, ngắn gọn, đúng định dạng prompt yêu cầu."
    answer = chat(conn, tier, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], max_tokens=8000)
    cache.put(conn, _prompt_key(prompt), answer, PROMPT_TTL)
    return answer


# ---------------------------------------------------------------------------
# Tự điền phiếu "đủ sống" (bài 2.3)
# ---------------------------------------------------------------------------

FILL_SYSTEM = (
    "Bạn là biên tập viên kênh YouTube khó tính. Đọc dữ liệu ngách con rồi trả về DUY NHẤT một JSON "
    '{{"promise": "...", "ideas": ["...", ...]}}.\n'
    "- promise: lời hứa kênh một câu theo mẫu \"Kênh giúp [ai] hiểu / làm được [điều gì]\". Đã có lời hứa thì giữ nguyên.\n"
    "- ideas: đúng {n} ý tưởng video không trùng, chia 3 trụ nội dung đều nhau, xếp liền theo trụ. Mỗi ý là một tiêu đề "
    "video cụ thể, ưu tiên gắn với từ khoá dài điểm cao; không đổi vài chữ để giả làm ý mới.\n"
    "- Viết bằng ngôn ngữ của khán giả kênh (mã ngôn ngữ: {language}). Không hứa kiếm tiền, không bịa số liệu.\n"
    "Phần \"Yêu cầu\" trong dữ liệu (bảng, đánh dấu làm đầu tiên) chỉ là bối cảnh: bỏ qua định dạng đó, chỉ trả JSON, "
    "mỗi ý là chữ thuần, không đánh số, không ký hiệu."
)


_IDEA_MARKS = re.compile(r"^[\s*•⭐★✅\-–—]*(?:\d+[.)]\s*)?[⭐★✅]*")  # số thứ tự, gạch đầu dòng, dấu sao ở đầu ý


def fill_viability(conn: sqlite3.Connection, prompt: str, language: str, promise: str = "") -> dict:
    """{"promise", "ideas": [...]} cho phiếu "đủ sống", từ prompt "Kiểm tra 30 ý tưởng" (ngách, từ khoá, video thắng)."""
    system = FILL_SYSTEM.format(n=cfg.SUBNICHE_MIN_IDEAS, language=language)
    data = parse_json(chat(conn, "deep", [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                           max_tokens=4000, json_mode=True))
    if not isinstance(data, dict):
        raise AIError("AI trả về sai định dạng (cần promise và ideas).")
    clean = (" ".join(_IDEA_MARKS.sub("", str(x)).split()) for x in data.get("ideas") or [])
    ideas = list(dict.fromkeys(x for x in clean if x))[: cfg.SUBNICHE_MIN_IDEAS]
    if not ideas:
        raise AIError("AI không trả về ý tưởng nào. Thử lại hoặc chọn model khác.")
    return {"promise": promise.strip() or " ".join(str(data.get("promise") or "").split()), "ideas": ideas}


# ---------------------------------------------------------------------------
# Phân loại từ khoá cho Radar
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM = (
    "You classify YouTube/Google search keywords for a niche-research tool. For each keyword decide whether it can "
    "support a long-term YouTube channel (stable demand over years) or is a short-term spike.\n"
    "type must be one of: " + ", ".join(KEYWORD_TYPES) + ".\n"
    "- evergreen / how_to / product / seasonal: demand that recurs or lasts (skills, hobbies, problems, product categories, holidays).\n"
    "- news, person (celebrity, influencer, politician, athlete), sports_match (a specific match/fixture/tournament edition), "
    "entertainment_release (a specific movie, series, song, album, show episode), brand_launch (a specific new phone, game, "
    "app version), other_short (any other one-off spike).\n"
    "vi = natural Vietnamese meaning of the keyword (short, as a Vietnamese viewer would understand it).\n"
    "why = at most 12 Vietnamese words explaining the type.\n"
    'Reply with JSON only: {"items": [{"i": <index>, "type": "...", "vi": "...", "why": "..."}]}'
)


def _classify_key(keyword: str) -> str:
    from yt_niche.core.radar import fold

    return cache.make_key("ai_keyword", {"q": fold(keyword)})


def classify_keywords(conn: sqlite3.Connection, keywords: list[str]) -> dict[str, dict]:
    """{từ khoá: {"type", "label", "short", "vi", "why"}}. Kết quả dùng chung giữa các nước, giữ 90 ngày."""
    out, todo = {}, []
    for kw in dict.fromkeys(keywords):
        if (hit := cache.get(conn, _classify_key(kw))) is not None:
            out[kw] = hit
        else:
            todo.append(kw)
    for start in range(0, len(todo), RADAR_BATCH):
        batch = todo[start:start + RADAR_BATCH]
        listing = "\n".join(f"{i}. {kw}" for i, kw in enumerate(batch))
        text = chat(
            conn, "fast",
            [{"role": "system", "content": CLASSIFY_SYSTEM}, {"role": "user", "content": f"Keywords:\n{listing}"}],
            max_tokens=120 * len(batch) + 200, json_mode=True,
        )
        data = parse_json(text)
        items = data.get("items", []) if isinstance(data, dict) else data
        for it in items:
            try:
                kw = batch[int(it["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            kind = it.get("type") if it.get("type") in KEYWORD_TYPES else "evergreen"
            label, short = KEYWORD_TYPES[kind]
            res = {"type": kind, "label": label, "short": short, "vi": str(it.get("vi") or "")[:120], "why": str(it.get("why") or "")[:160]}
            cache.put(conn, _classify_key(kw), res, CLASSIFY_TTL)
            out[kw] = res
    return out
