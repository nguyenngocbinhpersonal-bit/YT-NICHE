"""Ngách con (bài 2.3): cụm lặp lại ở video thắng, phân loại gợi ý tìm kiếm, kiểm tra tầng và "đủ sống"."""

from __future__ import annotations

import re
from collections import defaultdict

from yt_niche import config as cfg
from yt_niche.core.confidence import LOW
from yt_niche.core.money import RPM_LOW
from yt_niche.core.text import STOPWORDS, contains_phrase, no_space, normalize, tokens, word_count

# Bốn bộ lọc, mỗi tầng chỉ thêm một bộ lọc.
FILTERS = {
    "audience": "Đối tượng",
    "problem": "Vấn đề",
    "angle": "Góc kể / định dạng",
    "market": "Thị trường / ngôn ngữ",
}
FILTER_EXAMPLES = {
    "audience": "tâm lý học cho người đi làm 25–35 tuổi",
    "problem": "vì sao tiêu tiền khi buồn",
    "angle": "kể lịch sử như truyện; mỗi video một hiệu ứng tâm lý với nhân vật cố định",
    "market": "cùng chủ đề, nhắm thị trường ít người làm hơn",
}

SUBNICHE, LONG, SHORT = "Ngách con", "Từ khoá dài", "Từ khoá ngắn"


# ---------------------------------------------------------------------------
# Cụm lặp lại trong tiêu đề video thắng
# ---------------------------------------------------------------------------


def repeated_phrases(videos: list[dict], topic: str, *, min_channels: int | None = None, top: int = 15) -> list[dict]:
    """Cụm 2–6 từ (tiếng Việt: âm tiết) xuất hiện ở tiêu đề video thắng của ít nhất `min_channels` kênh khác nhau.

    Bỏ cụm chỉ gồm từ của chủ đề gốc, cụm mở đầu / kết thúc bằng hư từ, và cụm con có cùng độ phủ với cụm dài hơn.
    """
    min_channels = min_channels or cfg.SUBNICHE_MIN_CHANNELS
    topic_words = set(tokens(topic))
    channels: dict[str, set] = defaultdict(set)
    vids: dict[str, set] = defaultdict(set)
    for v in videos:
        words = tokens(v.get("title", ""))
        seen = set()
        for size in range(2, 7):
            for i in range(len(words) - size + 1):
                gram = words[i : i + size]
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                if all(w in topic_words or w in STOPWORDS for w in gram):
                    continue
                phrase = " ".join(gram)
                if phrase not in seen:
                    seen.add(phrase)
                    channels[phrase].add(v.get("channel_id"))
                    vids[phrase].add(v.get("video_id"))

    found = [
        {"phrase": p, "channels": len(chs), "videos": len(vids[p])}
        for p, chs in channels.items()
        if len(chs) >= min_channels
    ]
    # Cụm con ("học về") bị cụm dài hơn ("tâm lý học về tiền") phủ cùng số video thì bỏ.
    found.sort(key=lambda x: (-len(x["phrase"]), -x["videos"]))
    kept: list[dict] = []
    for item in found:
        if any(
            f" {item['phrase']} " in f" {k['phrase']} " and k["videos"] >= item["videos"] for k in kept
        ):
            continue
        kept.append(item)
    kept.sort(key=lambda x: (-x["channels"], -x["videos"], x["phrase"]))
    return kept[:top]


# ---------------------------------------------------------------------------
# Phân loại gợi ý tìm kiếm thành 3 cột
# ---------------------------------------------------------------------------


def _extra(keyword: str, seed: str, language: str) -> int | None:
    """Số từ thêm vào so với từ gốc; None nếu gợi ý không chứa đủ từ gốc."""
    if no_space(language):
        k, s = normalize(keyword).replace(" ", ""), normalize(seed).replace(" ", "")
        return len(k) - len(s) if s in k else None
    kw, sd = tokens(keyword), tokens(seed)
    if not all(w in kw for w in sd):
        return None
    return len(kw) - len(sd)


def keyword_kind(keyword: str, seed: str, language: str) -> str:
    """Ngách con: từ gốc ngắn + 1–3 từ (một bộ lọc). Từ khoá dài: đủ dài (3–6 từ ý). Còn lại: từ khoá ngắn.

    Khi từ gốc đã là cụm dài (ngách con tầng 2 như "tâm lý học về tiền"), các gợi ý mở rộng từ nó là từ khoá dài
    để chấm cho 10 video đầu, không phải ngách con mới.
    """
    min_len = cfg.KEYWORD_LONG_MIN_CHARS if no_space(language) else cfg.KEYWORD_LONG_MIN_WORDS
    extra = _extra(keyword, seed, language)
    max_extra = 4 if no_space(language) else 3
    if extra is not None and 1 <= extra <= max_extra and word_count(seed, language) < min_len:
        return SUBNICHE
    return LONG if word_count(keyword, language) >= min_len else SHORT


def evidence(phrase: str, videos: list[dict]) -> dict:
    """Số video thắng (và số kênh) có tiêu đề chứa đúng cụm này."""
    hits = [v for v in videos if contains_phrase(v.get("title", ""), phrase)]
    return {"videos": len(hits), "channels": len({v.get("channel_id") for v in hits})}


def columns(suggestions: list[dict], seed: str, language: str, winners: list[dict]) -> dict[str, list[dict]]:
    """Chia gợi ý thành 3 cột Ngách con / Từ khoá dài / Từ khoá ngắn, kèm bằng chứng từ video thắng."""
    out: dict[str, list[dict]] = {SUBNICHE: [], LONG: [], SHORT: []}
    seed_norm = normalize(seed)
    for s in suggestions:
        if normalize(s["keyword"]) == seed_norm:
            continue
        kind = keyword_kind(s["keyword"], seed, language)
        out[kind].append({**s, **evidence(s["keyword"], winners)})
    out[SUBNICHE].sort(key=lambda x: (-x["videos"], x["rank"]))
    return out


# ---------------------------------------------------------------------------
# Kiểm tra tầng và "đủ sống"
# ---------------------------------------------------------------------------


def layer_status(run: dict) -> dict:
    """Quy tắc "hẹp nhưng vẫn có người xem" cho một lần đào ngách con."""
    r = run["result"]
    layer = run.get("layer") or 1
    outliers = r["outlier_counts"]["total"]
    booms = len(r["small_boom_channels"])
    if layer > cfg.SUBNICHE_MAX_LAYER:
        return {
            "status": "video",
            "text": f"Tầng {layer}: thường là chủ đề video, không phải ngách. Dùng để lập danh sách video.",
        }
    if r["confidence"] == LOW or outliers == 0:
        why = "mẫu còn ít" if r["confidence"] == LOW else "không còn video vượt trội"
        return {"status": "too_narrow", "text": f"Quá hẹp ({why}): bỏ bớt một bộ lọc, lùi lên một tầng rồi đào lại."}
    if booms == 0:
        return {"status": "weak", "text": "Còn video vượt trội nhưng chưa có kênh nhỏ nào nổ: cửa cho kênh mới còn hẹp."}
    return {"status": "alive", "text": f"Còn sống: {outliers} video vượt trội, {booms} kênh nhỏ đang nổ."}


_NUMBERING = re.compile(r"^\s*(?:[-*•]|\d+[.)/:-]?)\s*")


def parse_ideas(text: str) -> list[str]:
    """Mỗi dòng một ý tưởng; bỏ số thứ tự / gạch đầu dòng và dòng trống."""
    ideas = []
    for line in (text or "").splitlines():
        line = _NUMBERING.sub("", line).strip()
        if line:
            ideas.append(line)
    return ideas


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def duplicate_ideas(ideas: list[str], threshold: float = 0.8) -> list[tuple[int, int]]:
    """Cặp (i, j) trùng hoặc gần trùng (chỉ đổi vài chữ): ≥ 80% từ giống nhau."""
    words = [set(w for w in tokens(x) if w not in STOPWORDS) or set(tokens(x)) for x in ideas]
    pairs = []
    for i in range(len(ideas)):
        for j in range(i + 1, len(ideas)):
            if normalize(ideas[i]) == normalize(ideas[j]) or _jaccard(words[i], words[j]) >= threshold:
                pairs.append((i, j))
    return pairs


def unique_ideas(ideas: list[str]) -> int:
    dup = {j for _, j in duplicate_ideas(ideas)}
    return len(ideas) - len(dup)


def _sentences(text: str) -> int:
    parts = [p for p in re.split(r"[.!?。！？]+", text.strip()) if p.strip()]
    return len(parts)


def viability(run: dict, ideas_text: str = "", promise: str = "") -> dict:
    """5 ô kiểm tra ngách con "đủ sống". ok = None nghĩa là chưa đủ dữ liệu để kết luận."""
    r = run["result"]
    outliers = r["outlier_counts"]["total"]
    booms = len(r["small_boom_channels"])
    ideas = parse_ideas(ideas_text)
    unique = unique_ideas(ideas)
    promise = (promise or "").strip()
    affiliates = run.get("affiliates") or []
    tier = r.get("rpm_tier")

    if not promise:
        promise_ok, promise_detail = None, "chưa viết"
    elif _sentences(promise) > 1:
        promise_ok, promise_detail = False, "đang có nhiều hơn một câu"
    else:
        promise_ok, promise_detail = True, promise

    if tier is None and not affiliates:
        money_ok, money_detail = None, "chưa chọn nhóm chủ đề (RPM) và chưa liệt kê affiliate"
    else:
        money_ok = (tier is not None and tier != RPM_LOW) or bool(affiliates)
        money_detail = f"RPM bậc {tier or 'chưa có'}, {len(affiliates)} sản phẩm affiliate"

    checks = [
        {
            "key": "outliers",
            "label": f"Còn video vượt trội từ {cfg.OUTLIER_OUTPERFORM:g}×",
            "ok": outliers > 0,
            "detail": f"{outliers} video",
        },
        {
            "key": "small_channels",
            "label": "Có ít nhất 1–2 kênh nhỏ làm đúng ngách đang tăng",
            "ok": booms >= 1,
            "detail": f"{booms} kênh nhỏ đang nổ",
        },
        {
            "key": "ideas",
            "label": f"Liệt kê được {cfg.SUBNICHE_MIN_IDEAS} ý tưởng video không trùng",
            "ok": None if not ideas else unique >= cfg.SUBNICHE_MIN_IDEAS,
            "detail": f"{unique} ý không trùng / {len(ideas)} dòng" if ideas else "chưa nhập",
        },
        {
            "key": "promise",
            "label": "Viết được lời hứa kênh trong một câu",
            "ok": promise_ok,
            "detail": promise_detail,
        },
        {"key": "money", "label": "Vẫn còn đường ra tiền", "ok": money_ok, "detail": money_detail},
    ]
    return {"checks": checks, "passed": all(c["ok"] for c in checks), "unique_ideas": unique}


# ---------------------------------------------------------------------------
# So Trends ngách mẹ với ngách con (bài 2.2)
# ---------------------------------------------------------------------------


def compare_trends(series: dict, parent: str) -> list[dict]:
    """series: {"5y": {chủ đề: điểm}, "12m": {...}} cùng thang. Ngách con đi lên khi ngách mẹ đi ngang là ứng viên tốt."""
    from yt_niche.core.trends_classify import relative_trend

    def info(topic: str) -> dict:
        v5 = [float(v) for _, v in (series.get("5y") or {}).get(topic) or []]
        v12 = [float(v) for _, v in (series.get("12m") or {}).get(topic) or []]
        t5 = relative_trend(v5) if v5 and sum(v5) else None
        t12 = relative_trend(v12) if v12 and sum(v12) else None
        last = v12[-13:] if v12 else []
        return {
            "topic": topic,
            "trend_5y": None if t5 is None else round(t5, 2),
            "trend_12m": None if t12 is None else round(t12, 2),
            "level": round(sum(last) / len(last), 1) if last else None,
        }

    def direction(t: float | None) -> str:
        if t is None:
            return "không có dữ liệu"
        return "đi lên" if t > 0.1 else "đi xuống" if t < -0.1 else "đi ngang"

    topics = list((series.get("5y") or series.get("12m") or {}).keys())
    rows = [info(t) for t in topics]
    p = next((r for r in rows if r["topic"] == parent), None)
    parent_flat_or_down = p is not None and direction(p["trend_12m"]) != "đi lên"
    for r in rows:
        r["direction_12m"] = direction(r["trend_12m"])
        r["direction_5y"] = direction(r["trend_5y"])
        r["is_parent"] = r["topic"] == parent
        r["good_candidate"] = not r["is_parent"] and parent_flat_or_down and r["direction_12m"] == "đi lên"
    return rows
