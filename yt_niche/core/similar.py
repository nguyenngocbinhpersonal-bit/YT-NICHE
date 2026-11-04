"""Score similar channels / videos from collected signals (no network calls).

Two parts, like the way TubeBuddy / vidIQ / NexLev rank "related":
- Content similarity: text comparison (TF-IDF over 4-character grams, tolerant to word variants such as "strong"/"stronger")
  between the source text and the candidate text. Normalized by the most similar candidate, so the number is relative
  within one lookup.
- Co-occurrence: a candidate that shows up in several sources (Up next, video search, channel search) is more likely a
  direct competitor.
The app uses no local embedding model. To compare across languages the source has several text versions (the original
and the titles YouTube auto-translates per hl); a candidate's similarity is the best of them.
"""

from __future__ import annotations

import math
from collections import Counter

from yt_niche import config as cfg
from yt_niche.core.text import STOPWORDS, tokens

GRAM = 4


def grams(text: str) -> Counter:
    out: Counter = Counter()
    for t in tokens(text):
        if t in STOPWORDS or len(t) < 2:
            continue
        padded = f" {t} "
        if len(padded) <= GRAM:
            out[padded] += 1
        else:
            out.update(padded[i : i + GRAM] for i in range(len(padded) - GRAM + 1))
    return out


class Corpus:
    """TF-IDF over a set of texts; similarity(a, b) is the cosine in [0, 1]."""

    def __init__(self, docs: dict[str, str]):
        self._grams = {k: grams(v) for k, v in docs.items()}
        df: Counter = Counter()
        for g in self._grams.values():
            df.update(g.keys())
        n = max(1, len(self._grams))
        self._idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self._vec = {k: self._weigh(g) for k, g in self._grams.items()}

    def _weigh(self, g: Counter) -> dict[str, float]:
        v = {t: (1 + math.log(c)) * self._idf.get(t, 1.0) for t, c in g.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._vec.get(a, {}), self._vec.get(b, {})
        if len(va) > len(vb):
            va, vb = vb, va
        return sum(x * vb.get(t, 0.0) for t, x in va.items())


def _relative(values: dict[str, float]) -> dict[str, float]:
    top = max(values.values(), default=0.0)
    return {k: (v / top if top > 0 else 0.0) for k, v in values.items()}


def cooccur_raw(signals: dict[str, float]) -> float:
    """Weighted sum of signals, plus a bonus for every distinct source type."""
    return sum(cfg.SIMILAR_SIGNAL_WEIGHTS.get(k, 1.0) * v for k, v in signals.items()) + 0.5 * sum(
        1 for v in signals.values() if v > 0
    )


def _content(source_texts: list[str], docs: dict[str, str]) -> dict[str, float]:
    """Similarity of each candidate to its best-matching source text, normalized by the most similar candidate."""
    src = {f"__src{i}__": t for i, t in enumerate(source_texts) if t}
    corpus = Corpus({**src, **docs})
    return _relative({k: max((corpus.similarity(s, k) for s in src), default=0.0) for k in docs})


def rank_channels(source_texts: list[str], candidates: list[dict]) -> list[dict]:
    """candidates: [{channel_id, text, signals: {type: count}, ...}] → adds content, cooccur, score; filtered and sorted."""
    if not candidates:
        return []
    content = _content(source_texts, {c["channel_id"]: c["text"] for c in candidates})
    cooc = _relative({c["channel_id"]: cooccur_raw(c["signals"]) for c in candidates})
    w = cfg.SIMILAR_CHANNEL_WEIGHTS
    out = []
    for c in candidates:
        cid = c["channel_id"]
        if content[cid] < cfg.SIMILAR_MIN_CONTENT:
            continue
        out.append(
            {
                **{k: v for k, v in c.items() if k != "text"},
                "content": round(content[cid], 3),
                "cooccur": round(cooc[cid], 3),
                "score": round(100 * (w["content"] * content[cid] + w["cooccur"] * cooc[cid]), 1),
            }
        )
    return sorted(out, key=lambda r: -r["score"])


def rank_videos(source_texts: list[str], candidates: list[dict]) -> list[dict]:
    """candidates: [{video_id, text, signals, ...}] → adds content, score; filtered and sorted."""
    if not candidates:
        return []
    content = _content(source_texts, {v["video_id"]: v["text"] for v in candidates})
    w = cfg.SIMILAR_VIDEO_WEIGHTS
    out = []
    for v in candidates:
        vid = v["video_id"]
        if content[vid] < cfg.SIMILAR_MIN_CONTENT:
            continue
        sig = min(sum(v["signals"].values()), cfg.SIMILAR_SIGNAL_CAP) / cfg.SIMILAR_SIGNAL_CAP
        out.append(
            {
                **{k: x for k, x in v.items() if k != "text"},
                "content": round(content[vid], 3),
                "score": round(100 * (w["content"] * content[vid] + w["signals"] * sig), 1),
            }
        )
    return sorted(out, key=lambda r: -r["score"])
