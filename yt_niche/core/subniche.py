"""Sub-niches (lesson 2.3): phrases repeated in winning videos, search-suggestion columns, layer check and "viable" check."""

from __future__ import annotations

import re
from collections import defaultdict

from yt_niche import config as cfg
from yt_niche.core.confidence import LOW
from yt_niche.core.money import RPM_LOW
from yt_niche.core.text import STOPWORDS, contains_phrase, no_space, normalize, tokens, word_count

# Four filters; each layer adds exactly one filter.
FILTERS = {
    "audience": "Audience",
    "problem": "Problem",
    "angle": "Angle / format",
    "market": "Market / language",
}
FILTER_EXAMPLES = {
    "audience": "psychology for office workers aged 25–35",
    "problem": "why we spend money when sad",
    "angle": "history told like a story; one psychological effect per video with a fixed character",
    "market": "same topic, aimed at an under-served market",
}

SUBNICHE, LONG, SHORT = "Sub-niche", "Long-tail keyword", "Short keyword"


# ---------------------------------------------------------------------------
# Phrases repeated in winning video titles
# ---------------------------------------------------------------------------


def repeated_phrases(videos: list[dict], topic: str, *, min_channels: int | None = None, top: int = 15) -> list[dict]:
    """2–6 word phrases (Vietnamese: syllables) found in winning video titles of at least `min_channels` different channels.

    Drops phrases made only of the topic's words, phrases that start / end with a stopword, and sub-phrases covered by a
    longer phrase with the same reach.
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
    # A sub-phrase ("about money") covered by a longer phrase ("psychology about money") in the same videos is dropped.
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
# Sort search suggestions into 3 columns
# ---------------------------------------------------------------------------


def _extra(keyword: str, seed: str, language: str) -> int | None:
    """Number of words added to the seed; None when the suggestion does not hold the whole seed."""
    if no_space(language):
        k, s = normalize(keyword).replace(" ", ""), normalize(seed).replace(" ", "")
        return len(k) - len(s) if s in k else None
    kw, sd = tokens(keyword), tokens(seed)
    if not all(w in kw for w in sd):
        return None
    return len(kw) - len(sd)


def keyword_kind(keyword: str, seed: str, language: str) -> str:
    """Sub-niche: short seed + 1–3 words (one filter). Long-tail keyword: long enough (3–6 meaningful words). Otherwise: short keyword.

    When the seed is already a long phrase (a layer-2 sub-niche such as "psychology about money"), suggestions that extend
    it are long-tail keywords to score for the first 10 videos, not new sub-niches.
    """
    min_len = cfg.KEYWORD_LONG_MIN_CHARS if no_space(language) else cfg.KEYWORD_LONG_MIN_WORDS
    extra = _extra(keyword, seed, language)
    max_extra = 4 if no_space(language) else 3
    if extra is not None and 1 <= extra <= max_extra and word_count(seed, language) < min_len:
        return SUBNICHE
    return LONG if word_count(keyword, language) >= min_len else SHORT


def evidence(phrase: str, videos: list[dict]) -> dict:
    """Number of winning videos (and channels) whose title holds this exact phrase."""
    hits = [v for v in videos if contains_phrase(v.get("title", ""), phrase)]
    return {"videos": len(hits), "channels": len({v.get("channel_id") for v in hits})}


def columns(suggestions: list[dict], seed: str, language: str, winners: list[dict]) -> dict[str, list[dict]]:
    """Split suggestions into the 3 columns Sub-niche / Long-tail keyword / Short keyword, with evidence from winning videos."""
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
# Layer check and "viable" check
# ---------------------------------------------------------------------------


def layer_status(run: dict) -> dict:
    """The "narrow but still watched" rule for one sub-niche dig."""
    r = run["result"]
    layer = run.get("layer") or 1
    outliers = r["outlier_counts"]["total"]
    booms = len(r["small_boom_channels"])
    if layer > cfg.SUBNICHE_MAX_LAYER:
        return {
            "status": "video",
            "text": f"Layer {layer}: usually a video topic, not a niche. Use it to build the video list.",
        }
    if r["confidence"] == LOW or outliers == 0:
        why = "small sample" if r["confidence"] == LOW else "no outperformer videos left"
        return {"status": "too_narrow", "text": f"Too narrow ({why}): remove one filter, go up one layer and dig again."}
    if booms == 0:
        return {"status": "weak", "text": "Outperformer videos remain but no small channel is booming yet: the door for a new channel is narrow."}
    return {"status": "alive", "text": f"Still alive: {outliers} outperformer videos, {booms} small booming channels."}


_NUMBERING = re.compile(r"^\s*(?:[-*•]|\d+[.)/:-]?)\s*")


def parse_ideas(text: str) -> list[str]:
    """One idea per line; strips numbering / bullets and blank lines."""
    ideas = []
    for line in (text or "").splitlines():
        line = _NUMBERING.sub("", line).strip()
        if line:
            ideas.append(line)
    return ideas


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def duplicate_ideas(ideas: list[str], threshold: float = 0.8) -> list[tuple[int, int]]:
    """Pairs (i, j) that are duplicates or near-duplicates (only a few words changed): ≥ 80% of words shared."""
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
    """The 5 checks that a sub-niche is "viable". ok = None means not enough data to conclude."""
    r = run["result"]
    outliers = r["outlier_counts"]["total"]
    booms = len(r["small_boom_channels"])
    ideas = parse_ideas(ideas_text)
    unique = unique_ideas(ideas)
    promise = (promise or "").strip()
    affiliates = run.get("affiliates") or []
    tier = r.get("rpm_tier")

    if not promise:
        promise_ok, promise_detail = None, "not written yet"
    elif _sentences(promise) > 1:
        promise_ok, promise_detail = False, "more than one sentence"
    else:
        promise_ok, promise_detail = True, promise

    if tier is None and not affiliates:
        money_ok, money_detail = None, "no topic category (RPM) picked and no affiliates listed"
    else:
        money_ok = (tier is not None and tier != RPM_LOW) or bool(affiliates)
        money_detail = f"RPM tier {tier or 'none'}, {len(affiliates)} affiliate products"

    checks = [
        {
            "key": "outliers",
            "label": f"Outperformer videos remain from {cfg.OUTLIER_OUTPERFORM:g}×",
            "ok": outliers > 0,
            "detail": f"{outliers} videos",
        },
        {
            "key": "small_channels",
            "label": "At least 1–2 small channels doing exactly this niche are growing",
            "ok": booms >= 1,
            "detail": f"{booms} small booming channels",
        },
        {
            "key": "ideas",
            "label": f"Can list {cfg.SUBNICHE_MIN_IDEAS} video ideas without duplicates",
            "ok": None if not ideas else unique >= cfg.SUBNICHE_MIN_IDEAS,
            "detail": f"{unique} unique ideas / {len(ideas)} lines" if ideas else "not entered yet",
        },
        {
            "key": "promise",
            "label": "Can write the channel promise in one sentence",
            "ok": promise_ok,
            "detail": promise_detail,
        },
        {"key": "money", "label": "A path to money remains", "ok": money_ok, "detail": money_detail},
    ]
    return {"checks": checks, "passed": all(c["ok"] for c in checks), "unique_ideas": unique}


# ---------------------------------------------------------------------------
# Compare parent-niche Trends with sub-niches (lesson 2.2)
# ---------------------------------------------------------------------------


def compare_trends(series: dict, parent: str) -> list[dict]:
    """series: {"5y": {topic: points}, "12m": {...}} on one scale. A sub-niche rising while the parent is flat is a good candidate."""
    from yt_niche.core.trends_classify import DOWN, FLAT, NO_DATA, UP, relative_trend

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
            return NO_DATA
        return UP if t > 0.1 else DOWN if t < -0.1 else FLAT

    topics = list((series.get("5y") or series.get("12m") or {}).keys())
    rows = [info(t) for t in topics]
    p = next((r for r in rows if r["topic"] == parent), None)
    parent_flat_or_down = p is not None and direction(p["trend_12m"]) != UP
    for r in rows:
        r["direction_12m"] = direction(r["trend_12m"])
        r["direction_5y"] = direction(r["trend_5y"])
        r["is_parent"] = r["topic"] == parent
        r["good_candidate"] = not r["is_parent"] and parent_flat_or_down and r["direction_12m"] == UP
    return rows
