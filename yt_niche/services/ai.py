"""AI through an OpenAI-compatible API (Command Code by default). Configured in Settings → AI; when it is off or fails,
the app runs as if there were no AI.

Only public data is sent (keywords, titles, channel numbers, prompt text). Never the email, the YouTube API key or
local paths.
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
TIERS = {"fast": "Fast (classify, translate)", "deep": "Deep (analysis, ideas)"}
FEATURES = {
    "radar": "Radar: classify keywords as short-lived / lasting and gloss their meaning (before spending Google Trends requests)",
    "prompts": "\"🤖 Run with AI\" button next to every built-in prompt (read dig results, sub-niches, 30 ideas, compare, translate keywords…)",
}
# reasoning_effort as Command Code defines it; "" = not sent (the model picks). When another provider rejects a value,
# the app switches to the nearest value it accepts (read from the "expected one of …" error).
EFFORTS = {"": "Default (not sent)", "off": "Thinking off", "low": "Low", "medium": "Medium", "high": "High",
           "xhigh": "Very high", "max": "Max"}
EFFORT_RANK = {"off": 0, "none": 0, "minimal": 0, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 5}
LEGACY_EFFORT = {"minimal": "off", "none": "off"}
DEFAULT_CONFIG = {
    "enabled": False,
    "base_url": cfg.AI_BASE_URL,
    "model_fast": "",
    "model_deep": "",
    "effort_fast": "low",
    "effort_deep": "medium",
    "models": [],  # list fetched from /models when "Test connection" is clicked
    "features": {k: True for k in FEATURES},
    "max_requests_per_day": cfg.AI_MAX_REQUESTS_PER_DAY,
}
TIMEOUT = 120
CLASSIFY_TTL = 90 * 86400
PROMPT_TTL = 30 * 86400
RADAR_BATCH = 40

# Keyword type returned by the AI → label; types with short=True are dropped by the Radar before the Trends check.
KEYWORD_TYPES = {
    "evergreen": ("Evergreen", False),
    "seasonal": ("Seasonal", False),
    "how_to": ("How-to / skill", False),
    "product": ("Product / service", False),
    "news": ("News / event", True),
    "person": ("Celebrity", True),
    "sports_match": ("Match / tournament", True),
    "entertainment_release": ("New film / music / show", True),
    "brand_launch": ("Product / game launch", True),
    "other_short": ("Other short-lived", True),
}


# Extra tokens for the thinking part per effort level (added to each task's max_tokens).
REASONING_BUDGET = {"minimal": 1000, "low": 3000, "medium": 8000, "high": 20000, "xhigh": 32000, "max": 48000}
# Optional parameter → words that identify it in the error when the model does not support it.
OPTIONAL_PARAMS = {"reasoning_effort": ("reasoning",), "response_format": ("response_format", "json"), "temperature": ("temperature",)}
# Some models still think with effort "off" (e.g. deepseek-v4.1-flash on Command Code, measured 10/2026: ~450 thinking
# tokens for 2 keywords). On the first hit (empty answer because tokens ran out) remember that model and from then on
# always leave at least THINK_FLOOR tokens.
THINK_FLOOR = 4000
THINKS_TTL = 180 * 86400
_UNSUPPORTED: set[tuple[str, str]] = set()
_EFFORT_FIX: dict[tuple[str, str], str] = {}


def _nearest_effort(error: str, effort: str) -> str | None:
    """Error like 'expected one of "off"|"low"|…': pick the accepted value closest to the one being sent."""
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
# Configuration
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
# Daily usage
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
# API calls
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
    """Kept separate so tests can replace it."""
    try:
        r = httpx.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise AIError(f"Could not reach the AI: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    return r.json()


def list_models(base_url: str | None = None) -> list[str]:
    """Call /models (checks the key and lists model names). Not counted against the daily usage."""
    if not api_key():
        raise AIError("No API key yet.")
    url = (base_url or cfg.AI_BASE_URL).rstrip("/") + "/models"
    try:
        r = httpx.get(url, headers=_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise AIError(f"Could not reach {url}: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise AIError(_error_text(r))
    body = r.json()
    items = body.get("data", body) if isinstance(body, dict) else body
    return sorted({m["id"] if isinstance(m, dict) else str(m) for m in items})


def _thinks_key(model: str) -> str:
    return f"ai_thinks:{model}"


def _send(url: str, payload: dict, model: str, effort: str | None) -> dict:
    """Send; when an optional parameter is rejected, fix or drop it, remember that, and send again."""
    while True:
        try:
            return _post(url, payload)
        except AIError as e:
            if "reasoning_effort" in payload and (alt := _nearest_effort(str(e), payload["reasoning_effort"])):
                _EFFORT_FIX[(model, effort)] = alt  # the provider rejects this level: use the nearest one it accepts
                payload["reasoning_effort"] = alt
                continue
            # The model does not support an optional parameter (effort, JSON mode, temperature): drop it, remember, retry.
            bad = next((k for k, words in OPTIONAL_PARAMS.items() if k in payload and any(w in str(e).lower() for w in words)), None)
            if bad is None:
                raise
            payload.pop(bad)
            _UNSUPPORTED.add((bad, model))


def chat(conn: sqlite3.Connection, tier: str, messages: list[dict], max_tokens: int = 4000, json_mode: bool = False) -> str:
    c = get_config(conn)
    if not enabled(conn):
        raise AIError("AI is off or not fully configured (Settings → AI).")
    if usage_today(conn)["requests"] >= c["max_requests_per_day"]:
        raise AIError(f"Used all {c['max_requests_per_day']} AI requests for today (change the limit in Settings → AI).")
    model = c[f"model_{tier}"]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    budget = 0
    if (effort := c.get(f"effort_{tier}")) and ("reasoning_effort", model) not in _UNSUPPORTED:
        effort = _EFFORT_FIX.get((model, effort), effort)
        payload["reasoning_effort"] = effort
        budget = REASONING_BUDGET.get(effort, 0)
    if cache.get(conn, _thinks_key(model)):
        budget = max(budget, THINK_FLOOR)
    # Thinking models count their thinking against max_tokens: add room so tokens are left for the answer.
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
            raise AIError(f"Malformed AI response: {str(body)[:200]}") from e
        if content.strip():
            return content
        if choice.get("finish_reason") != "length":
            raise AIError(f"Model {model} returned an empty answer. Try again, lower the effort or pick another model.")
        if attempt == 0 and not cache.get(conn, _thinks_key(model)):
            # Ran out of tokens while thinking (even with thinking off): remember this model, add room and ask once more.
            cache.put(conn, _thinks_key(model), True, THINKS_TTL)
            payload["max_tokens"] = max_tokens + max(budget, THINK_FLOOR)
            continue
        break
    raise AIError(
        f"Model {model} spent all {payload['max_tokens']:,} tokens thinking and never answered. "
        "Lower the effort (Settings → AI) or pick another model."
    )


def parse_json(text: str):
    """Extract JSON from the answer (strip ```json … ``` fences and stray text around it)."""
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
    raise AIError(f"The AI did not return valid JSON: {text[:150]}")


# ---------------------------------------------------------------------------
# Running a built-in prompt
# ---------------------------------------------------------------------------


def _prompt_key(prompt: str) -> str:
    return "ai_prompt:" + hashlib.sha1(prompt.encode()).hexdigest()


def cached_answer(conn: sqlite3.Connection, prompt: str) -> str | None:
    return cache.get(conn, _prompt_key(prompt))


def run_prompt(conn: sqlite3.Connection, prompt: str, tier: str = "deep") -> str:
    system = "You are a YouTube niche research expert. Answer in English, concisely, in exactly the format the prompt asks for."
    answer = chat(conn, tier, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], max_tokens=8000)
    cache.put(conn, _prompt_key(prompt), answer, PROMPT_TTL)
    return answer


# ---------------------------------------------------------------------------
# Auto-fill the "viable niche" check (lesson 2.3)
# ---------------------------------------------------------------------------

FILL_SYSTEM = (
    "You are a demanding YouTube channel editor. Read the sub-niche data and return ONLY one JSON object "
    '{{"promise": "...", "ideas": ["...", ...]}}.\n'
    "- promise: a one-sentence channel promise in the form \"This channel helps [who] understand / do [what]\". "
    "If a promise is already given, keep it as is.\n"
    "- ideas: exactly {n} video ideas with no duplicates, split evenly across 3 content pillars, grouped by pillar. Each idea "
    "is a specific video title, preferably built on a high-scoring long-tail keyword; do not change a few words to fake a new idea.\n"
    "- Write in the language of the channel's audience (language code: {language}). Never promise income, never invent numbers.\n"
    "The \"Task\" section in the data (table, mark the first ones to make) is context only: ignore that format, return JSON only, "
    "each idea as plain text with no numbering and no symbols."
)


_IDEA_MARKS = re.compile(r"^[\s*•⭐★✅\-–—]*(?:\d+[.)]\s*)?[⭐★✅]*")  # numbering, bullets and stars at the start of an idea


def fill_viability(conn: sqlite3.Connection, prompt: str, language: str, promise: str = "") -> dict:
    """{"promise", "ideas": [...]} for the "viable niche" check, from the "Check 30 ideas" prompt (niche, keywords, winning videos)."""
    system = FILL_SYSTEM.format(n=cfg.SUBNICHE_MIN_IDEAS, language=language)
    data = parse_json(chat(conn, "deep", [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                           max_tokens=4000, json_mode=True))
    if not isinstance(data, dict):
        raise AIError("The AI returned the wrong shape (promise and ideas expected).")
    clean = (" ".join(_IDEA_MARKS.sub("", str(x)).split()) for x in data.get("ideas") or [])
    ideas = list(dict.fromkeys(x for x in clean if x))[: cfg.SUBNICHE_MIN_IDEAS]
    if not ideas:
        raise AIError("The AI returned no ideas. Try again or pick another model.")
    return {"promise": promise.strip() or " ".join(str(data.get("promise") or "").split()), "ideas": ideas}


# ---------------------------------------------------------------------------
# Keyword classification for the Radar
# ---------------------------------------------------------------------------

# The "vi" field (and the radar_keyword.ai_vi column) keeps its legacy name; it now holds the keyword's meaning in English.
CLASSIFY_SYSTEM = (
    "You classify YouTube/Google search keywords for a niche-research tool. For each keyword decide whether it can "
    "support a long-term YouTube channel (stable demand over years) or is a short-term spike.\n"
    "type must be one of: " + ", ".join(KEYWORD_TYPES) + ".\n"
    "- evergreen / how_to / product / seasonal: demand that recurs or lasts (skills, hobbies, problems, product categories, holidays).\n"
    "- news, person (celebrity, influencer, politician, athlete), sports_match (a specific match/fixture/tournament edition), "
    "entertainment_release (a specific movie, series, song, album, show episode), brand_launch (a specific new phone, game, "
    "app version), other_short (any other one-off spike).\n"
    "vi = natural English meaning of the keyword (short, as an English-speaking viewer would understand it; keywords in other "
    "languages are translated).\n"
    "why = at most 12 English words explaining the type.\n"
    'Reply with JSON only: {"items": [{"i": <index>, "type": "...", "vi": "...", "why": "..."}]}'
)


def _classify_key(keyword: str) -> str:
    from yt_niche.core.radar import fold

    return cache.make_key("ai_keyword", {"q": fold(keyword)})


def classify_keywords(conn: sqlite3.Connection, keywords: list[str]) -> dict[str, dict]:
    """{keyword: {"type", "label", "short", "vi", "why"}}. Results are shared across markets and kept for 90 days."""
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
