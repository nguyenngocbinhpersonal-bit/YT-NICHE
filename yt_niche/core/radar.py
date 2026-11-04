"""Keyword radar: pick Google Trends categories, drop short-lived trends, score potential.

Funnel: category queries (free) → drop short-lived entities (people, songs, shows, events) by Google's entity type
→ drop keywords almost nobody searched in the last 30 days on YouTube Search or Google Search
→ score the few best ones on YouTube.
"""

from __future__ import annotations

import re
import unicodedata
from yt_niche import config as cfg
from yt_niche.core.stats import log_scale

# Short-lived categories in Google Trends' official category tree (ids are the same in every language).
EXCLUDED_CATEGORIES = {
    16: "News",
    112: "Broadcast & Network News",
    184: "Celebrities & Entertainment News",
    507: "Gossip & Tabloid News",
    396: "Politics",
    398: "Campaigns & Elections",
    1077: "Sports News",
    63: "Weather",
    1209: "World News",
    572: "Local News",
    408: "Newspapers",
    784: "Business News",
    785: "Technology News",
    1253: "Health News",
    569: "Events & Listings",
    1273: "Live Sporting Events",
    1085: "Movie Listings & Theater Showtimes",
    891: "Concerts & Music Festivals",
    358: "TV Shows & Programs",
}
# Root groups dominated by short-lived news: scan their sub-categories (minus the short-lived ones) instead of the whole group.
SPLIT_ROOTS = {3}  # Arts & Entertainment

# Google entity types that show a query is a person, a new release, a team / league, or an event.
SHORT_TERM_TYPES = re.compile(
    r"singer|rapper|musician|band|group|song|album|single|actor|actress|film|movie|tv |television|series|show|"
    r"episode|politician|president|minister|player|footballer|athlete|team|club|league|match|tournament|cup|"
    r"election|event|storm|hurricane|typhoon|earthquake|celebrity|model|influencer|youtuber|streamer|comedian|"
    r"personality|presenter|host|journalist|businessperson|entrepreneur|director|producer|k-pop|idol",
    re.I,
)

# Keywords useless for a YouTube channel even when evergreen (lottery, weather...). Users can add more on the Radar page.
# The Vietnamese entries are real search terms to block, so they stay as they are.
DEFAULT_BLOCKLIST = ["xổ số", "xo so", "lottery", "lotto", "thời tiết", "weather", "karaoke", "login", "đăng nhập"]

PENDING, PASSED, EXCLUDED, IGNORED, WATCHING = "pending", "passed", "excluded", "ignored", "watching"
STATUS_LABEL = {
    PENDING: "to check",
    PASSED: "passed",
    EXCLUDED: "excluded",
    IGNORED: "ignored",
    WATCHING: "watching",
}


def fold(text: str) -> str:
    """Strip accents, lowercase, collapse whitespace: 'Zoë Café' → 'zoe cafe' (Vietnamese d-with-stroke → d)."""
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def flatten(tree: dict) -> list[dict]:
    """[{id, name, depth, parent}] in tree order; an id repeated on several branches is kept once."""
    out, seen = [], set()

    def walk(node, depth, parent):
        if node["id"] not in seen:
            seen.add(node["id"])
            out.append({"id": node["id"], "name": node["name"], "depth": depth, "parent": parent})
        for child in node.get("children", []):
            walk(child, depth + 1, node["id"])

    for child in tree.get("children", []):
        walk(child, 1, 0)
    return out


def default_categories(tree: dict) -> list[int]:
    """Root groups minus News; for Arts & Entertainment take the sub-categories minus the short-lived ones."""
    flat = flatten(tree)
    roots = [c for c in flat if c["depth"] == 1 and c["id"] not in EXCLUDED_CATEGORIES]
    out = [c["id"] for c in roots if c["id"] not in SPLIT_ROOTS]
    out += [c["id"] for c in flat if c["depth"] == 2 and c["parent"] in SPLIT_ROOTS and c["id"] not in EXCLUDED_CATEGORIES]
    return out


def selectable_categories(tree: dict, max_depth: int = 2) -> list[dict]:
    return [c for c in flatten(tree) if c["depth"] <= max_depth and c["id"] not in EXCLUDED_CATEGORIES]


# ---------------------------------------------------------------------------
# Pick candidates from one category scan
# ---------------------------------------------------------------------------


def blocked(query: str, blocklist: list[str]) -> bool:
    q = f" {fold(query)} "
    return any(f" {fold(b)} " in q for b in blocklist if b.strip())


def pick_new(found: dict[str, list[tuple[str, int]]], known: set[str], blocklist: list[str], limit: int) -> list[dict]:
    """New keywords worth checking: rising from RADAR_MIN_RISING%, sorted by % growth; top up from the "top" list when short."""
    # In both "top" and "rising" = many searches and growing → check first. Only in "rising" is usually a term almost
    # nobody searched (growing from 0), likely to be dropped as "too few searches".
    in_top = {fold(q) for q, _ in found.get("top", [])}
    rising = sorted(
        ((q, v) for q, v in found.get("rising", []) if v >= cfg.RADAR_MIN_RISING),
        key=lambda x: (fold(x[0]) in in_top, x[1]), reverse=True,
    )
    out, seen = [], set()
    for kind, items in (("rising", rising), ("top", found.get("top", []))):
        for q, v in items:
            key = fold(q)
            if len(out) >= limit:
                return out
            if not key or key in known or key in seen or blocked(q, blocklist):
                continue
            seen.add(key)
            out.append({"keyword": " ".join(q.split()), "kind": kind, "value": v})
    return out


# ---------------------------------------------------------------------------
# Checks: entity type and 30-day search volume
# ---------------------------------------------------------------------------


def short_term_entity(query: str, entities: list[dict]) -> dict | None:
    """The short-lived entity the query refers to (title matches the query and the type is a person / release / event)."""
    q = fold(query)
    for e in entities:
        title = fold(e.get("title", ""))
        if title and (title in q or q in title) and SHORT_TERM_TYPES.search(e.get("type", "")):
            return e
    return None


SOURCE_LABEL = {"youtube": "YouTube Search", "web": "Google Search"}


def volume_30d(points: list) -> dict:
    """30-day search volume of one source. Trends scores 0–100 against the highest day in the range: a low average or
    many zero days means few searches (Google rounds down to 0 when there are too few), on and off."""
    values = [v for _, v in points]
    if not any(values):
        return {"ok": False, "avg": 0.0, "zeros": 1.0, "change": 0.0}
    avg = sum(values) / len(values)
    zeros = sum(v == 0 for v in values) / len(values)
    half = len(values) // 2
    first, last = values[:half] or values, values[half:]
    change = (sum(last) / len(last) - sum(first) / len(first)) / max(avg, 1)  # second half vs first half
    ok = zeros <= cfg.RADAR_30D_MAX_ZERO_SHARE and avg >= cfg.RADAR_30D_MIN_AVG
    return {"ok": ok, "avg": round(avg, 1), "zeros": round(zeros, 2), "change": round(max(-1.0, min(1.0, change)), 2)}


def judge_volume(stats: dict[str, dict]) -> dict:
    """stats: {"youtube": volume_30d, "web": volume_30d} (when the first source fails, the second is absent).
    {"ok", "reason", "volume", "durability" 0–100, "trend_30d"}: missing volume on any source excludes the keyword."""
    for source, s in stats.items():
        if s["ok"]:
            continue
        label = SOURCE_LABEL[source]
        if not s["avg"]:
            reason = f"{label}: no data for the last 30 days (almost nobody searches it in this market)"
        elif s["zeros"] > cfg.RADAR_30D_MAX_ZERO_SHARE:
            reason = f"{label}: {s['zeros']:.0%} of the last 30 days are 0 (too few searches)"
        else:
            reason = f"{label}: 30-day average only {s['avg']:.0f}/100 of the highest day (low, on-and-off search volume)"
        return {"ok": False, "reason": reason, "volume": stats}
    avg = sum(s["avg"] for s in stats.values()) / len(stats)
    trend = stats.get("web", next(iter(stats.values())))["change"]
    reason = "; ".join(
        f"{SOURCE_LABEL[k]} average {s['avg']:.0f}/100, searched on {1 - s['zeros']:.0%} of days" for k, s in stats.items()
    )
    # Steadiness: an average of 50/100 or more (not bunched into a few days) = 100 points.
    return {"ok": True, "reason": reason, "volume": stats, "durability": round(min(100.0, 2 * avg), 1), "trend_30d": trend}


# ---------------------------------------------------------------------------
# Potential score
# ---------------------------------------------------------------------------


def momentum(kind: str, value: int, trend_12m: float | None) -> float:
    if kind == "rising":
        return log_scale(max(value, 1), *cfg.RADAR_MOMENTUM_LOG_RANGE)
    return max(0.0, min(100.0, 50 + 100 * (trend_12m or 0)))  # top queries: follow the recent trend


def persistence(seen_days: int) -> float:
    return min(100.0, 25.0 * (seen_days - 1))


def potential(row: dict) -> float | None:
    """0–100 score for a keyword that passed the volume filter; without a YouTube score the weights are re-spread over the other 3 parts."""
    shape = row.get("trend") or {}
    if row.get("status") == EXCLUDED or not shape.get("ok"):
        return None
    parts = {
        # trend_12m: keywords checked with the earlier 5-year shape (until 10/2026)
        "momentum": momentum(row["kind"], row["value"], shape.get("trend_30d", shape.get("trend_12m"))),
        "durability": shape["durability"],
        "persistence": persistence(row["seen_days"]),
    }
    if row.get("yt_score") is not None:
        parts["youtube"] = row["yt_score"]
    weights = {k: w for k, w in cfg.RADAR_WEIGHTS.items() if k in parts}
    return round(sum(parts[k] * w for k, w in weights.items()) / sum(weights.values()), 1)
